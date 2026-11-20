"""GAPS.md for a workspace, from the records a translator pass leaves (tools/AutoOnboard/autoonboard.py's gaps stage; also run on its
own, read-only, on any workspace): which functions the translator did not translate, grouped by what stopped them;
which were refused or never judged; and what the console's kit lacks that the game calls.

usage: py -3.12 tools/AutoOnboard/onboard_gaps.py --workspace WS --console dreamcast|saturn|gamecube|xbox360 --out GAPS.md
           [--name NAME]

A translator pass that is not finished (state/wave_game.json has a scope, its translate step is not done) makes a
partial GAPS.md: it says so first, with how many functions are in finished chunks and how many are left.
Per scope function (tools/wave_game.py step 1's list, else core/scope), the first record that says something, newest
first: state/wave_game_translate.tsv (translate_backlog --rows: verdict and why), the ledger (a translated match, a
kept translated FAIL, the translator's "no C" reason, an agent's match), state/translator_verdicts.json (a verdict
only), the harness (meta.json "unsupported", too few valid vectors), and the wave's list of functions left without a
harness. Reasons are grouped with core/audit.norm (addresses and numbers folded).
Refused functions are also counted by the instruction that caused it: the oracle's reason names it ("system
instruction mftb at ...", "Direct3D pack/unpack vpkd3d128 at ... (semantics unverified)"); on the Xbox 360 a function the
catalog scan refused before any prepare (catalog_inputs.json "refuse") is named from its own words
(targets/xbox360_ppc/tools/ppcscan.refusing_ops).
Kit gaps, Dreamcast: port/tools/kit_features.py (the SDK functions the kit's modules define, read from port/sdk/*.c)
against the library functions the scope calls (state/graph.tsv callees outside the scope) and their names in the
registry (readable/names/*.json); an unnamed library function is counted as not known, never as a gap. Xbox 360:
targets/xbox360_ppc/port/tools/kit_imports.py (the kernel and XAM ordinals the kit answers, read from its runtime
source) against the imports the game's code calls (port/tools/survey.py: call sites per import thunk). GameCube: there
is no kit, so the section says so and lists what one would supply, from the game's code (tools/AutoOnboard/onboard_gc.kit_section:
hardware register blocks, SDK and OS calls by matched name). Any other console: "kit gaps: not known - no feature list
for <console>".

Nearest existing: decomp.py audit (core/audit.py) - groups finished agent-route rows (failed, skipped) by declared
blocker kinds and counts pending ones; it reads no translator rows or verdicts and has no kit side. Reused here for
its norm() and the scope. The X360 and GameCube compile_all.py group one translator's compile failures only.
"""
import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys

TOOL = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TOOL), str(TOOL / "tools"), str(TOOL / "port" / "tools")]
from core.audit import norm  # noqa: E402
from wave_game import given_up, recorded  # noqa: E402  (tools/: the translate pass's per-function records)

EXAMPLES = 4
X360 = TOOL / "targets" / "xbox360_ppc"
# The GameCube oracle's words too: "cache/system instruction mftb" (at the end), "FPSCR move mtfsf (rounding mode)".
INSTR = re.compile(r"(?:instruction|pack/unpack|CALLOTHER \w+ in|FPSCR move) ([a-z][\w.#]*)(?: at|\s*\(|$)")


def read(path, default):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def scope_of(ws):
    st = read(ws / "state" / "wave_game.json", {})
    if st.get("scope"):
        return list(st["scope"]), st, "the wave's scope (state/wave_game.json)"
    r = subprocess.run([sys.executable, "-B", str(TOOL / "tools" / "wave_game.py"), "_scope", str(ws)],
                       capture_output=True, text=True, timeout=1800, cwd=TOOL)
    if r.returncode:
        raise SystemExit(f"onboard_gaps: no scope for {ws}: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout.strip().splitlines()[-1]), st, "tools/wave_game.py _scope"


def short(s, n=110):
    return norm(" ".join(str(s or "").split()))[:n] or "(no reason recorded)"


def instruction(text):
    """The instruction an oracle refusal names, or None."""
    m = INSTR.search(str(text or ""))
    return m.group(1) if m else None


def oracle_refusal(text):
    mn = instruction(text)
    return f"oracle refused: `{mn}` - {short(str(text).replace(mn, 'OP'))}" if mn else f"oracle refused: {short(text)}"


def catalog_refusals(ws):
    """Xbox 360: {addr: reason} of the functions the catalog scan refused (catalog_inputs.json "refuse"), each with the
    instruction that decided it, named from its words in image.bin."""
    cat = read(ws / "catalog_inputs.json", None)
    if not cat:
        return {}
    sys.path.insert(0, str(X360 / "tools"))
    import ppcscan
    img = (ws / "image.bin").read_bytes() if (ws / "image.bin").is_file() else b""
    base = (read(ws / "image.json", {}) or {}).get("load_address", 0x82000000)
    out = {}
    for f in cat.get("functions", []):
        if f.get("refuse") and not f["refuse"].startswith("frame helper"):
            ops = ppcscan.refusing_ops(img, base, int(f["addr"], 16), f["size"]) if img else {}
            top = max(sorted(ops), key=ops.get) if ops else None
            out[f["addr"]] = (f"catalog refused (ppcscan, before any prepare): `{top}` - {f['refuse']}" if top else
                              f"catalog refused (ppcscan, before any prepare): {f['refuse']}" + (
                                  " - none of its vector ops is one the target refuses (an estimate, a Direct3D "
                                  "pack/unpack): an older catalog's rule, which refused every vector function; "
                                  "catalog_inputs.json made again would offer it" if "VMX" in f["refuse"] else ""))
    return out


def outcome(a, ws, led, rows, tv, st, refusals=None):
    """(kind, reason): kind translated | not translated | refused | not tried."""
    r = led.get(a) or {}
    if r.get("status") == "matched" and r.get("source") == "translated":
        return "translated", ""
    row = rows.get(a)
    if row:
        v, stage, harness, why = row
        if v == "PASS":
            return ("not translated", f"PASS on a harness that is not current ({short(harness)}): not recorded") \
                if harness != "current" else ("translated", "")
        if v == "NOTRANSLATE":
            return "not translated", f"no C: {short(why)}"
        if v == "FAIL":
            return "not translated", f"FAIL at {stage or '?'}: {short(why)}"
        if instruction(why):
            return "refused", oracle_refusal(why)
        return "refused", {"NOVECTORS": "too few valid vectors (oracle)", "NOJUDGE": f"tester error: {short(why)}"}.get(
            v, f"{v}: {short(why)}")
    if r.get("translated_fail"):
        att = r["translated_fail"].get("att") or {}
        return "not translated", f"FAIL at {att.get('stage') or '?'}: {short(att.get('summary'))}"
    tx = r.get("translator") or {}
    if tx and not tx.get("ok") and tx.get("why"):
        return "not translated", f"no C: {short(tx['why'])}"
    if r.get("status") == "matched":
        return "not translated", "matched by an agent" + (f" (translator {tv[a]})" if a in tv else
                                                          " (no translator record)")
    if a in tv:
        return "not translated", f"translator {tv[a]} (no detail kept)"
    if refusals and a in refusals:
        return "refused", refusals[a]
    meta = read(ws / "harness" / a / "meta.json", None)
    if meta is None:
        failed = a in set(st.get("no_harness") or [])
        return ("refused", "prepare failed (no current harness)") if failed else ("not tried", "never prepared")
    if meta.get("unsupported"):
        return "refused", oracle_refusal(meta["unsupported"])
    return "not tried", "prepared, never translated"


def partial(st, sc, ws=None):
    """'' for a finished translator pass (or none started by the wave), else what is done and what is left."""
    if not st.get("scope") or (st.get("steps") or {}).get("translate"):
        return ""
    done = len(set(st.get("translated") or []) & set(sc)) if ws is None else len(recorded(ws, st, None) & set(sc))
    return (f"{done} of {len(sc)} functions ({done / max(len(sc), 1):.0%}) recorded: the translator pass is "
            f"not finished, {len(sc) - done} left. The translated count also holds functions translated before the "
            "pass (the estimate's sample). Everything below is this state; run tools/AutoOnboard/autoonboard.py again to go on")


def x360_kit_section(ws):
    """The imports the game's code calls (port/tools/survey.py) against those the kit answers (kit_imports.py)."""
    sys.path.insert(0, str(X360 / "port" / "tools"))
    import kit_imports
    import survey
    kf = kit_imports.features()
    sv = survey.survey(ws)
    used = [i for i in sv["imports"] if i["sites"]]
    lacks, other, noeff = [], [], []
    for i in used:
        key = kit_imports.lib_key(i["lib"])
        if key is None:
            other.append(i)
            continue
        row = kf["libraries"][key].get(str(i["ord"]))
        (lacks if row is None else noeff if row["no_effect"] else []).append(i)
    names = read(X360 / "port" / "data" / "export_names.json", {})

    def nm(i):
        key = kit_imports.lib_key(i["lib"]) or str(i["lib"])
        return (names.get(key.split(".")[0], {}).get(str(i["ord"])) or ["(no name in export_names.json)"])[0]
    have = {k: len(v) for k, v in kf["libraries"].items()}
    out = [f"Kit list: {', '.join(f'{k} {n} ordinals' for k, n in have.items())}, generated from the kit's runtime "
           "source (targets/xbox360_ppc/port/tools/kit_imports.py, this checkout's kit). Game side: the imports with "
           "call sites in the game's code (port/tools/survey.py over image.bin).", "",
           f"- imports the game calls: {len(used)} of {len(sv['imports'])} it declares; the kit answers "
           f"{len(used) - len(lacks) - len(other)}", f"- called, and the kit has no case for them: {len(lacks)}"]
    out += [f"  - `{nm(i)}` ({i['lib']} #{i['ord']}, {i['sites']} call site(s) in {i['callers']} function(s))"
            for i in sorted(lacks, key=lambda i: -i["sites"])[:80]] + (["  - ..."] if len(lacks) > 80 else [])
    out.append(f"- answered with 0 and no effect (the kit logs them once): {len(noeff)}"
               + "".join(f"; `{nm(i)}`" for i in noeff[:10]))
    out.append(f"- in a library the kit does not route at all: {len(other)}"
               + "".join(f"; {i['lib']} #{i['ord']}" for i in other[:10]))
    return out, {"kit gaps": len(lacks), "imports called": len(used)}


def kit_section(ws, console, sc):
    if console == "xbox360":
        if not (ws / "catalog_inputs.json").is_file():
            return ["kit gaps: not known - no catalog_inputs.json (the extract stage writes it)"], {"kit gaps": "not known"}
        return x360_kit_section(ws)
    if console == "gamecube":
        if not (ws / "state" / "catalog.json").is_file():
            return ["kit gaps: not known - no state/catalog.json (the census stage writes it)"], {"kit gaps": "not known"}
        import onboard_gc
        return onboard_gc.kit_section(ws, sc)
    if console == "saturn":
        if not (ws / "state" / "function_bodies.txt").is_file():
            return ["kit gaps: not known - no state/function_bodies.txt (the census stage writes it)"], {
                "kit gaps": "not known"}
        import onboard_sat
        return onboard_sat.kit_section(ws, sc)
    if console != "dreamcast":
        return [f"kit gaps: not known - no feature list for {console}"], {"kit gaps": "not known"}
    import kit_features
    kf = kit_features.features()
    graph = ws / "state" / "graph.tsv"
    if not graph.is_file():
        return ["kit gaps: not known - no call graph (state/graph.tsv; `decomp.py systems --graph` writes it)"], {
            "kit gaps": "not known"}
    scs, calls = set(sc), collections.Counter()
    for line in graph.read_text(encoding="utf-8").splitlines():
        p = line.split("\t")
        if p[0] in scs and len(p) > 1:
            calls.update(c for c in p[1].split(",") if c and c not in scs)
    names = {}
    for f in sorted((ws / "readable" / "names").glob("*.json")):
        for addr, e in (read(f, {}).get("functions") or {}).items():
            m = re.search(r"(\w+)\s*\(", (e or {}).get("proto", ""))
            if m:
                names[addr] = m.group(1)
    named = {a: names[a] for a in calls if a in names and not names[a].startswith("fn_")}
    have = kf["names"]
    gaps = sorted((n, a) for a, n in named.items() if n not in have)
    unnamed = len(calls) - len(named)
    out = [f"Kit list: {len(have)} SDK functions in {len(kf['modules'])} modules, generated from port/sdk/*.c "
           "(port/tools/kit_features.py). Game side: library functions the scope calls (state/graph.tsv callees "
           "outside the scope), named from readable/names.", "",
           f"- called library functions: {len(calls)}; named: {len(named)}; the kit defines {len(named) - len(gaps)}",
           f"- named, and no kit module defines them: {len(gaps)}"]
    out += [f"  - `{n}` ({a}, {calls[a]} caller(s))" for n, a in gaps[:60]] + (["  - ..."] if len(gaps) > 60 else [])
    out.append(f"- unnamed: {unnamed} - kit gaps for these: not known (tools/sdk_fid.py names SDK functions from a Sega "
               "SDK; then run this again)" if unnamed else "- unnamed: 0")
    if not named:
        out.insert(0, "kit gaps: not known - the game's library functions are not named yet")
    return out, {"kit gaps": len(gaps) if named else "not known", "library calls unnamed": unnamed}


NOT_FOUND = {
    "dreamcast": "- Functions the census did not find are in no row here. Entry tables not scanned: the census finds what "
                 "analysis reaches, and Toy Commander needed 3.5% more, added from entry tables and by hand. Next step: "
                 "tools/data_funcs.py on the workspace, then tables of code pointers by hand (docs/NEW_GAME.md step 2).",
    "xbox360": "- Functions the census did not find are in no row here. Too Human's catalogue (pdata, pointers, calls) "
               "lacked 6,549 entries its port runs (5,928 code between functions, 507 inside or mid entries, 114 call "
               "targets), about 7.4% more; tools/catfeed.py adds them, on branch x360-judge-catfeed, not on master yet.",
    "gamecube": "- Functions the census did not find are in no row here. Data tables of code pointers are not scanned: "
                "Skies of Arcadia Legends' census (6,751) lacked 4 script opcode handlers reached only through such a "
                "table; seed them with project.json seed_entries (targets/gamecube_ppc/SKIES_TRANSLATE_SCORE.md)."}


def table(groups, total):
    out = ["| what stopped them | functions | examples |", "|---|---|---|"]
    for reason, addrs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        out.append(f"| {reason.replace('|', '/')} | {len(addrs)} | {', '.join(sorted(addrs)[:EXAMPLES])} |")
    return out if groups else ["None."]


def write(ws, console, out, name=None):
    """GAPS.md at out; returns the numbers for a verdict."""
    ws = pathlib.Path(ws)
    sc, st, src = scope_of(ws)
    led = (read(ws / "state" / "ledger.json", {}) or {}).get("functions") or {}
    tv = (read(ws / "state" / "translator_verdicts.json", {}) or {}).get("functions") or {}
    rows = {}
    tsv = ws / "state" / "wave_game_translate.tsv"
    if tsv.is_file():
        for line in tsv.read_text(encoding="utf-8", errors="replace").splitlines():
            p = line.split("\t")
            if len(p) >= 5:
                rows[p[0]] = p[1:5]  # a later line for the same function is the newer one
    by = {"translated": [], "not translated": collections.defaultdict(list), "refused": collections.defaultdict(list),
          "not tried": collections.defaultdict(list)}
    refusals = {a: why for a, (_, why) in given_up(ws, None).items()}  # a prepare that failed or was killed at its limit
    refusals.update(catalog_refusals(ws) if console == "xbox360" else {})
    for a in sc:
        kind, why = outcome(a, ws, led, rows, tv, st, refusals)
        (by[kind].append(a) if kind == "translated" else by[kind][why].append(a))
    instr = collections.Counter()
    for why, addrs in by["refused"].items():
        m = re.search(r"`([^`]+)`", why)
        if m:
            instr[m.group(1)] += len(addrs)
    n = {k: (len(v) if k == "translated" else sum(map(len, v.values()))) for k, v in by.items()}
    part = partial(st, sc, ws)
    kit, kn = kit_section(ws, console, sc)
    proj = read(ws / "project.json", {})
    text = [f"# Gaps: {name or proj.get('name') or ws.name} ({console})", "",
            f"Workspace `{ws}`; generated by tools/AutoOnboard/onboard_gaps.py from its records, not by hand. Scope: {len(sc)} "
            f"functions ({src}); translated {n['translated']}, not translated {n['not translated']}, refused or not "
            f"judged {n['refused']}, not tried {n['not tried']}. Records read: "
            + ", ".join(x for x, ok in (("wave_game_translate.tsv", tsv.is_file()), ("ledger.json", bool(led)),
                                        ("translator_verdicts.json", bool(tv))) if ok) + ".", "",
            ] + ([f"**PARTIAL:** {part}", ""] if part else []) + [
            "## 1. Not translated, by what stopped them", ""] + table(by["not translated"], n["not translated"]) + [
            "", "## 2. Refused or not judged", ""] + table(by["refused"], n["refused"]) + ([
            "", "By the instruction that caused the refusal:", "", "| instruction | functions |", "|---|---|"]
            + [f"| `{k}` | {v} |" for k, v in instr.most_common()] if instr else []) + [
            "", "## 3. Not tried", ""] + table(by["not tried"], n["not tried"]) + [
            "", f"## 4. Kit features the game uses that the {console} kit lacks", ""] + kit + [
            "", "## 5. Not counted above", "", NOT_FOUND.get(console, NOT_FOUND["dreamcast"])]
    pathlib.Path(out).write_text("\n".join(text) + "\n", encoding="utf-8", newline="\n")
    top = max(by["not translated"].items(), key=lambda kv: len(kv[1]), default=(None, []))
    return {"scope": len(sc), **({"partial": part.split(":")[0]} if part else {}),
            **({"refused by instruction": ", ".join(f"{k} {v}" for k, v in instr.most_common(6))} if instr else {}),
            "translated": n["translated"], "not translated": n["not translated"],
            "refused/unjudged": n["refused"], "not tried": n["not tried"], **kn,
            "top reason": f"{top[0]} ({len(top[1])})" if top[0] else "-"}


class Gaps:
    """The gaps stage's adapter (onboard_stages: a Built adapter for any console; the kit part says what it knows)."""
    kind = "built"
    tools = ("tools/AutoOnboard/onboard_gaps.py", "tools/wave_game.py", "core/audit.py", "port/tools/kit_features.py")

    def calls(self, ctx):
        return ["tools/AutoOnboard/onboard_gaps.write(WS, console, .work/onboard/GAPS.md)"]

    def run(self, ctx):
        import onboard_stages as S
        out = ctx.project / S.STATE_DIR / "GAPS.md"
        num = write(S.ws_of(ctx), ctx.console, out, ctx.name)
        return {"numbers": num, "paths": {"gaps": str(out)},
                "message": (f"PARTIAL ({num['partial']}): " if num.get("partial") else "")
                           + f"{num['translated']} translated, {num['not translated']} not translated, "
                           f"{num['refused/unjudged']} refused or unjudged, {num['not tried']} not tried; "
                           f"kit gaps {num['kit gaps']}; top reason {num['top reason']}",
                "next": [f"Read {out}: the commonest reason is where a translator fix pays most"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--console", required=True, choices=("dreamcast", "saturn", "gamecube", "xbox360"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--name")
    a = ap.parse_args(argv)
    print(json.dumps(write(a.workspace, a.console, a.out, a.name)))


if __name__ == "__main__":
    main()
