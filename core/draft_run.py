"""Machine readable files before an agent (batch 23; readable-tx): `refine.py draft` and `refine.py run --draft`.

Two machine sources, the first that serves wins:
- the readable pass (core/rtx, readable-tx): the translator's raw C lifted to readable C (structured control flow,
  folded expressions, typed locals, fields and named constants from the headers), judge_machine();
- the draft (batch 23): the matched raw C with the registry's names, make() / judge().

make(): the draft of one function: core/draft's rewrite of matched/ADDR.c, the registry prototype as the header (the
raw parameters renamed to the prototype's by storage, core/readable_abi), `(void)name;` for a parameter the body
does not use, and `return;` for a value the prototype does not return. It is saved as runs/ADDR/readable/draft.c.
judge(): the full readable judge (every lint, dev and hidden vectors), then the readability gate (readable_gaps:
no register-named variables, raw parameter or address names, or address labels) and the adoption gate
(core/draft_gate, batch 24: short, no literal the headers name): a draft is adopted only when all pass, as
`source: draft` (no agent, no tokens).
before_agent(): refine.refine's hook. Modes (refine.py run --draft, config readable_draft):
- off: nothing;
- adopt: a passing draft is adopted, else the agent starts as before (from the do-nothing stub);
- start (the default; batch 23 A/B: fewer tokens, no loss): as adopt, and a draft that does not pass is the
  agent's starting work.c (TASK.md says what it is).
The row keeps what the machine files did: "draft": {"verdict", "stage", "why", "at", "kind"} (kind: machine for the
readable pass, draft for the draft). An adopted file's row has "readable_by": "machine": no agent has named its locals
or given its numbers their meaning yet (core/refine tells a later agent so; an agent's pass sets "agent").
The adoption gate has no length limit by default (the user's decision, 2026-10-23: an agent reviews the file when it
next works on the function); config readable_draft_max_lines sets one.
"""
import re
import shutil

from . import draft, draft_gate, paths, readable, readable_abi, refine
from . import readable_check as rcheck
from .compare import IMAGE_BASE, WORD
from .raw_names import mask

MODES = ("off", "adopt", "start")
# Not named C: register mirrors (r4, fr5, f12), raw parameter and address names, Ghidra's automatic names, gotos.
GAP = re.compile(r"\b(?:r\d{1,2}|fr\d{1,2}|f(?:[0-9]|1[0-5])|dr\d{1,2}|xf\d{1,2}|fpul|macl|mach|gbr|param_\w+|"
                 r"fn_8c[0-9a-f]{6}|g_8c[0-9a-f]{6}|DAT_8c[0-9a-f]{6}|L_8c[0-9a-f]{6}|[a-z]{1,4}Var\d+|"
                 r"local_[0-9a-f]+|in_\w+|unaff_\w+|extraout_\w+|goto|args|TcArgs)\b")


def names_for(cfg, reg, addr, meta, raw_text):
    """core/draft.Names for one function (registry, extents, literal pool, callees)."""
    from . import global_extents
    from .readable_context import pool_constants
    globs = {}
    for a, e in reg.globals.items():
        decl = e["decl"].strip().rstrip(";")
        name = readable.global_name(decl)
        at = re.search(rf"\b{re.escape(name)}\b", decl)
        globs[int(a, 16)] = (name, decl[:at.start()].strip(), re.findall(r"\[([^\]]*)\]", decl[at.end():]))
    fns = {a: readable.parse_proto(e["proto"])[1] for a, e in reg.functions.items()}
    image = cfg["binary_path"].read_bytes()

    def reader(v, size):
        off = v - IMAGE_BASE
        return int.from_bytes(image[off:off + size], "big" if WORD == ">I" else "little") if 0 <= off <= len(image) - size else None
    pool = {int(a, 16): reader for a, _ in pool_constants(cfg, meta, raw_text)}
    callees = {}
    for c in meta["callees"]:
        try:
            proto = reg.proto(c["addr"], raw_sig=c["signature"])
            place = readable_abi.callee_place(reg, c, proto, rcheck.callee_storage(paths.HARNESS / addr, c))
            callees[c["addr"]] = (readable.parse_proto(proto)[1], place, reg.kind(readable.parse_proto(proto)[0]),
                                  readable.is_variadic(proto))
        except readable.RegistryError:
            continue
    raw_ok = {c["addr"] for c in meta["callees"]} | set(rcheck.data_functions(reg, addr, meta))
    return draft.Names(globs, fns, reg.kind, global_extents.for_process(cfg, reg), pool, callees,
                       raw_ok - set(reg.functions))


def header(text, reg, addr, meta, proto):
    """text with the raw definition's header replaced by proto and the raw parameters renamed by storage."""
    from .sync_text import exported
    found = exported(mask(text)[0])
    if not found:
        return text
    start, close, brace, end = found
    new = readable.parse_proto(proto)[2]
    try:
        where = readable_abi.own_storages(reg, addr, proto, meta)
    except readable.RegistryError:
        where = []
    by_storage = {s: n for s, (_, n) in zip(where, new)}
    renames = {n: by_storage[s] for n, s in readable_abi.param_storage(meta).items() if s in by_storage}
    from .lookalike import rewrite
    body = rewrite(text[brace:end + 1], renames)
    code = mask(body)[0]
    unused = [n for _, n in new if not re.search(rf"\b{re.escape(n)}\b", code)]
    if unused:
        nl = body.index("\n") + 1 if "\n" in body else 1
        body = body[:nl] + "".join(f"    (void){n};\n" for n in unused) + body[nl:]
    if reg.kind(readable.parse_proto(proto)[0]) == "v":
        body = re.sub(r"\breturn\s+([^;]+);", lambda m: "return;" if draft.side_effect_free(m.group(1))
                      else m.group(0), body)
    elif readable.parse_proto(readable.raw_proto(meta["signature"]))[0] == "void":
        # the prototype returns what the raw C's last call (a tail call) leaves in r0/fr0
        body = re.sub(r"(\n[ \t]*)(?:\(void\)\s*)?([A-Za-z_]\w*\s*\([^;]*\))\s*;(\s*\}\s*)$", r"\1return \2;\3", body)
    return text[:start] + proto.strip().rstrip(";") + "\n" + body + text[end + 1:]


def make(cfg, reg, addr, meta):
    """(draft text, the raw-runtime idioms it keeps: names)."""
    raw = (paths.MATCHED / f"{addr}.c").read_text(encoding="utf-8", errors="replace")
    names = names_for(cfg, reg, addr, meta, raw)
    proto = reg.proto(addr, meta)
    text = draft.strip_comments(raw)
    text = re.sub(r'#\s*include\s+"tc_rt\.h"', '#include "game.h"', text, count=1)
    text = draft.rewrite_memory(text, names)
    text = draft.rewrite_tc_calls(text, names.callees, reg.kind(readable.parse_proto(proto)[0]) == "v")
    text = draft.rewrite_values(text, names)
    text = header(text, reg, addr, meta, proto)
    left = sorted({m.group(0) for m in re.finditer(r"\b(?:rd8|rd16|rd32|rdf32|wr8|wr16|wr32|wrf32|tc_callf?|tc_last[rf]|TcArgs)\b",
                                                   mask(text)[0])})
    return text.rstrip() + "\n", left


def readable_gaps(text):
    """Why a passing draft does not read like a readable file: raw-named identifiers left in its code."""
    return sorted({m.group(0) for m in GAP.finditer(mask(text)[0])})


def draft_path(addr):
    return paths.RUNS / addr / "readable" / "draft.c"


def machine_path(addr):
    return paths.RUNS / addr / "readable" / "machine.c"


def judge_machine(cfg, reg, addr, meta, log=print):
    """(path, result, gaps) of the readable pass's machine file (runs/ADDR/readable/machine.c), judged as judge()
    judges a draft; None when the pass has no file for the function (config readable_machine false: never)."""
    if not cfg.get("readable_machine", True):
        return None
    from .rtx import source
    try:
        text = source.machine(cfg, reg, addr, meta)
    except (source.Unsupported, readable.RegistryError) as e:
        log(f"machine file: none ({e})"[:300])
        return None
    path = machine_path(addr)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    res = rcheck.check_readable(cfg, addr, str(path), paths.BUILD / addr / "rd_machine", reg=reg)
    if res["verdict"] != "PASS":
        return path, res, []
    gate = draft_gate.reasons(text, max_lines=cfg.get("readable_draft_max_lines", draft_gate.MAX_LINES))
    return path, res, readable_gaps(text) + gate


def candidates(cfg, reg, addr, meta, log=print):
    """[(kind, path, result, gaps)]: the machine file, then the draft unless the machine file already passes the
    judge (a draft keeps the same numbers, so it is no better start and no likelier adoption)."""
    out = []
    try:
        m = judge_machine(cfg, reg, addr, meta, log)
    except Exception as e:  # noqa: BLE001  (a readable-pass bug: the draft serves, as before)
        log(f"machine file: not judged ({type(e).__name__}: {e})"[:300])
        m = None
    if m is not None:
        out.append(("machine",) + m)
        if m[1]["verdict"] == "PASS":
            return out
    try:
        out.append(("draft",) + judge(cfg, reg, addr, meta, log))
    except (readable.RegistryError, OSError, ValueError, KeyError) as e:
        log(f"draft: not made ({type(e).__name__}: {e})"[:300])
    return out


def judge(cfg, reg, addr, meta, log=print):
    """(path, result, gaps): the draft written to runs/ADDR/readable/draft.c, its full judge, its readability gaps."""
    text, _ = make(cfg, reg, addr, meta)
    path = draft_path(addr)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    res = rcheck.check_readable(cfg, addr, str(path), paths.BUILD / addr / "rd_draft", reg=reg)
    if res["verdict"] != "PASS":
        return path, res, []
    gate = draft_gate.reasons(text, max_lines=cfg.get("readable_draft_max_lines", draft_gate.MAX_LINES))
    return path, res, readable_gaps(text) + gate


def why(res, gaps):
    if res["verdict"] == "PASS":
        return "passes; not adopted: " + ", ".join(gaps[:8]) if gaps else "passes"
    return f"{res['stage']}: " + ("; ".join((res.get("lint") or []) + (res.get("build") or [])[:2])[:300] or
                                  f"{res.get('failing')}/{res.get('total')} vectors differ")


def adoptable(res, gaps):
    return res["verdict"] == "PASS" and res.get("stage") == "compare" and not res.get("run_error") and not gaps


def replaceable(addr):
    """None when `refine.py draft --adopt` may write readable/src/ADDR.c: there is no file yet, or verify recorded it
    STALE (it fails the current judge on a current harness); else why not. A passing file is never replaced, nor a
    harness_stale one (its raw C fails too: nothing can pass until it is prepared again)."""
    if not (readable.SRC / f"{addr}.c").exists():
        return None
    status = refine.load_ledger().get(addr, {}).get("status")
    return None if status == "stale" else f"has a readable file ({status})"


def adopt(addr, path, res, kind="draft"):
    """Copy the passing machine file to readable/src and record it (`source: draft`, `readable_by: machine`). A file
    already there (a STALE one, replaceable()) is backed up first, as core/readopt does (state/readopt_backup)."""
    import datetime
    from . import header_snapshot, readopt
    readable.SRC.mkdir(parents=True, exist_ok=True)  # a fresh workspace has no readable/src yet
    src, backup = readable.SRC / f"{addr}.c", None
    if src.exists():
        readopt.BACKUP.mkdir(parents=True, exist_ok=True)
        backup = readopt.BACKUP / f"{addr}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.c"
        shutil.copyfile(src, backup)
    shutil.copyfile(path, src)
    row = refine.load_ledger().get(addr, {})
    atts = list(row.get("attempts") or [])
    atts.append({"n": len(atts) + 1, "kind": kind, "at": refine.now(), "verdict": "PASS",
                 "stage": res.get("stage"), "headers": header_snapshot.active_hash(),
                 **({"backup": paths.show(backup)} if backup else {})})
    refine.update(addr, attempts=atts, source="draft", readable_by="machine",
                  draft={"verdict": "PASS", "at": refine.now(), "kind": kind})
    return refine.record_pass(addr, header_snapshot.active_hash())


def before_agent(cfg, reg, addr, meta, mode, log=print):
    """refine.refine's hook, after the preflight: ("adopted", status) when a passing draft was adopted; ("start",
    (path, TASK.md note)) when the agent starts from the draft; None otherwise."""
    if mode not in ("adopt", "start"):
        return None
    found = candidates(cfg, reg, addr, meta, log)
    if not found:
        return None
    for kind, path, res, gaps in found:
        if adoptable(res, gaps):
            refine.update(addr, draft={"verdict": "PASS", "stage": res.get("stage"), "why": "passes",
                                       "at": refine.now(), "kind": kind})
            log(f"{kind}: passes the judge and reads as named C: adopted (source: draft), no agent")
            return "adopted", adopt(addr, path, res, kind)
    # the start: the first that passes the judge, else the draft (as before the readable pass), else the machine file
    passing = [c for c in found if c[2]["verdict"] == "PASS"]
    kind, path, res, gaps = (passing or [c for c in found if c[0] == "draft"] or found)[0]
    note = why(res, gaps)
    refine.update(addr, draft={"verdict": res["verdict"], "stage": res.get("stage"), "why": note[:300],
                               "at": refine.now(), "kind": kind})
    log(f"{kind}: {note}"[:300])
    if mode != "start":
        return None
    if kind == "machine":
        return "start", (path, "\n## work.c is a machine readable file\n\nThe readable pass (core/rtx) lifted the "
                               "translator's raw C: control flow, types, fields and the constants it could name from "
                               "the headers. No agent has named its locals (n1, x1, ...) or given its numbers their "
                               f"meaning yet. Result: {note[:400]}\nKeep what it does; name what it means.\n")
    return "start", (path, "\n## work.c is a machine draft\n\nThe matched raw C with the registry's names, prototypes "
                           "and direct calls where the tool could make them; what it could not convert is still raw "
                           f"(rd/wr, tc_call, register names). Result: {note[:400]}\nMake it readable C that passes.\n")


def first(cfg, addr, mode, log=print):
    """fn_claim.cycle's step before refine.refine: before_agent for a function with no readable file whose raw C
    and harness are current (refine.refine reports those); None otherwise or when the draft cannot be judged."""
    if mode not in ("adopt", "start") or (readable.SRC / f"{addr}.c").exists():
        return None
    if refine.raw_missing(addr) or refine.harness_stale_kept(cfg, addr):  # refine() takes a PASS
        return None
    meta = paths.read_json(paths.HARNESS / addr / "meta.json")
    try:
        return before_agent(cfg, readable.Registry(), addr, meta, mode, log)
    except refine.PassNotRecorded:
        raise
    except Exception as e:  # noqa: BLE001  (a draft that cannot be judged changes nothing: the agent runs)
        log(f"draft: not judged ({type(e).__name__}: {e})"[:300])
        return None


def command(cfg, addrs, adopt_ok=False, workers=4, log=print):
    """`refine.py draft TARGETS [--adopt]`: judge each function's draft (runs/ADDR/readable/draft.c); --adopt adopts
    a passing, readable one for a function without a readable file, or whose file verify recorded STALE (the old
    file backed up; replaceable()), under its claim. Returns {addr: outcome}."""
    import concurrent.futures
    from . import fn_claim
    reg = readable.Registry()

    def one(addr):
        meta = paths.read_json(paths.HARNESS / addr / "meta.json")
        if not meta or refine.raw_missing(addr):
            return addr, "skipped: no harness or no matched raw C"
        try:
            with fn_claim.claim(addr, "draft", fn_claim.wait_s(cfg)):
                found = candidates(cfg, reg, addr, meta, log)
                if not found:
                    return addr, "not made: no machine file and no draft"
                for kind, path, res, gaps in found:
                    if adoptable(res, gaps):
                        kept = replaceable(addr)
                        if adopt_ok and kept is None:
                            stale = (readable.SRC / f"{addr}.c").exists()
                            adopt(addr, path, res, kind)
                            return addr, f"ADOPTED (source: draft, {kind})" + (", replaced STALE" if stale else "")
                        return addr, f"adoptable ({kind})" + (f"; not adopted: {kept}" if adopt_ok else "")
                kind, path, res, gaps = found[0]
                return addr, f"{kind}: {why(res, gaps)}"
        except fn_claim.Busy as e:
            return addr, f"busy: {e}"
        except (readable.RegistryError, OSError, ValueError, KeyError) as e:
            return addr, f"not made: {type(e).__name__}: {e}"[:300]
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max(1, workers)) as ex:
        for addr, got in ex.map(one, addrs):
            out[addr] = got
            log(f"{addr}: {got}"[:300])
    return out
