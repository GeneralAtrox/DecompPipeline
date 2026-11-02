"""Name a game's unnamed functions and parameters in one cheap pass, so the machine lift's files read as named C.

A machine-lifted file that passes the judge is only adopted when no raw name is left in it (core/draft_run.GAP), and
a game without designed names has fn_ADDR callees and param_N parameters everywhere: Vigilante 8 had 802 of 828 such
files, Spawn (designed) passed 30 of 30. Each batch is one Haiku session with no tools: what each function does
(the lifted text when given with --drafts, cut short, else what its raw C calls and touches, with the callees'
names so far), answered with one JSON line per function. Only the name and the parameter names come from the
model: the types, the order and the storage stay the raw signature's, so a wrong name cannot change behaviour, and
the judge still decides every file. A designer can rename an entry later (it carries `"machine": "name_pass"`).

  py -3.12 tools/name_pass.py --project WS [--scope SYSTEM,...] [--batch 30] [--slots 4] [--drafts DIR] [--dry-run]

Then `refine.py header` must pass, and `refine.py sync-names` rewrites the files already readable. Cost goes to
state/name_pass.json. Nearest existing: core/global_infer and core/rodata_strings - they name data from how it is
used, with no model; a function's purpose needs reading. A designer (docs/NEW_GAME.md section 5) writes types and
headers as well, at a strong model's price, one system at a time.
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SYSTEM = """You name functions in a decompiled Dreamcast game. Each function below is given as machine-made C, or as a \
list of what it calls and touches. Reply with one JSON object per line and nothing else, one per function, in the \
same order:
{"addr":"8c000000","name":"System_VerbNoun","params":["first","second"]}
name: a short system word, an underscore, then what the function does, in CamelCase (Car_ApplyDamage, Menu_DrawCursor, \
Sound_StopAll). Use what the callees, strings and globals tell you; when they tell you little, describe the shape \
of the work (List_FindByKey, Struct_CopyFields, Value_Clamp). Never leave a raw name and never write an address.
params: one lower-case C identifier per parameter given, in order, saying what it holds (count, entity, out_pos); \
exactly as many as the function shows; [] when it shows none. Don't think long: a plain name is fine."""

NAME = re.compile(r"[A-Z][A-Za-z0-9]*_[A-Za-z0-9_]*[A-Za-z][A-Za-z0-9_]*")
PARAM = re.compile(r"[a-z][a-z0-9_]{1,30}")
# what the lift calls its own locals, what C reserves, and the runtime's short names
TAKEN = re.compile(r"(?:n|x|vec|lhs|rhs|out|tmp)\d+|auto|break|case|char|const|continue|default|do|double|else|enum|extern|"
                   r"float|for|goto|if|inline|int|long|register|restrict|return|short|signed|sizeof|static|struct|switch|"
                   r"typedef|union|unsigned|void|volatile|while|bool|true|false|[us](?:8|16|32|64)|f32|f64|"
                   r"rd(?:8|16|32|f)|wr(?:8|16|32|f)|sh4_\w+|tc_\w+|u2f|f2u|memcpy|memset|strlen|strcmp|abs|min|max|sqrt|"
                   r"sin|cos|tan|floor|ceil|time|index|exit|div|printf|sprintf|rand|sq_start|fabs|pow|log|exp|signal|read|"
                   r"write|open|close|select|remove|rename|y0|y1|j0|j1|yn|jn")
MAX_TEXT = 1400
HEX_ADDR = re.compile(r"\b0x(8c[0-9a-f]{6})u?\b", re.I)
RAW_FN = re.compile(r"\bfn_(8c[0-9a-f]{6})\b")


def strip_comments(text):
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))


def valid_param(name):
    from core import draft_run
    return bool(PARAM.fullmatch(name)) and not TAKEN.fullmatch(name) and not draft_run.GAP.fullmatch(name)


def valid_name(name):
    from core import draft_run
    return bool(NAME.fullmatch(name)) and not draft_run.GAP.search(name) and not re.search(r"8c[0-9a-f]{6}", name)


def raw_of(addr):
    """A function's raw C; empty when it has none yet (its callers still need its name)."""
    from core import paths
    path = paths.MATCHED / f"{addr}.c"
    return path.read_text(errors="replace") if path.is_file() else ""


def callees(raw_text):
    """The fn_ADDR a raw C file names, its own first if any."""
    return list(dict.fromkeys(RAW_FN.findall(strip_comments(raw_text))))


def describe(addr, proto, raw_text, draft, reg):
    """One function's entry in the prompt. Names known so far replace the raw ones; a global is named once."""
    def fn_name(a):
        e = reg.functions.get(a)
        return raw_name_of(e["proto"]) if e else f"fn_{a}"

    if draft:
        body = strip_comments(draft).replace('#include "game.h"', "").strip()
        body = RAW_FN.sub(lambda m: fn_name(m.group(1)), body)
        body = re.sub(r"\b(?:g|DAT)_(8c[0-9a-fA-F]{6})\b", lambda m: global_of(reg, m.group(1)) or m.group(0), body)
        if len(body) > MAX_TEXT:
            body = body[:MAX_TEXT] + "\n... (cut)"
        return f"--- {addr}\n{body}"
    code = strip_comments(raw_text)
    seen, calls, data = set(), [], []
    for a in callees(code):
        if a != addr:
            calls.append(fn_name(a))
    for m in HEX_ADDR.finditer(code):
        a = m.group(1).lower()
        if a in seen or a in reg.functions:
            continue
        seen.add(a)
        if g := global_of(reg, a):
            text = reg.globals[a].get("text") if a in reg.globals else None
            data.append(f'{g} "{text[:40]}"' if text else g)
    lines = [f"--- {addr}", proto, f"size {len(code)} bytes of raw C"]
    if calls:
        lines.append("calls " + ", ".join(calls[:24]))
    if data:
        lines.append("uses " + ", ".join(data[:24]))
    return "\n".join(lines)


def raw_name_of(proto):
    from core.raw_names import proto_name
    return proto_name(proto) or "?"


def global_of(reg, addr):
    from core import readable
    e = reg.globals.get(addr)
    return readable.global_name(e["decl"]) if e else None


def parse_reply(text, want):
    """{addr: (name, [params])} of the answer's lines that are for a function in want. Other lines are ignored."""
    out = {}
    for line in text.splitlines():
        line = line.strip().strip("`")
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        a = str(d.get("addr", "")).lower().removeprefix("0x")
        if a in want and isinstance(d.get("name"), str) and isinstance(d.get("params"), list):
            out[a] = (d["name"], [str(p) for p in d["params"]])
    return out


def build_entry(reg, addr, meta, name, params, used):
    """The names entry of a function: the raw signature's types and order under the model's names. A name in use takes
    its address as a suffix; a parameter name that is not usable, or the wrong number of them, falls back to argN."""
    from core import readable, readable_abi
    ret, _, raw = readable.parse_proto(readable.raw_proto(meta["signature"]))
    if name in used:
        name = f"{name}_{addr}"
    used.add(name)
    fixed = [p for p in raw if p[1] != "..."] if raw else []
    names, seen = [], set()
    good = len(params) == len(fixed)
    for i, (_, _n) in enumerate(fixed):
        p = params[i] if good and valid_param(params[i]) and params[i] not in seen else f"arg{i + 1}"
        seen.add(p)
        names.append(p)
    args = [f"{t} {n}" for (t, _), n in zip(fixed, names)]
    if readable.is_variadic(readable.raw_proto(meta["signature"])):
        args.append("...")
    proto = f"{ret} {name}({', '.join(args) or 'void'})"
    entry = {"proto": proto, "machine": "name_pass"}
    storage = [p["storage"] if p["storage"] != "stack" else f"stack+{p['stack_offset']}" for p in meta["params"]]
    if len(storage) == len(fixed) and readable_abi.storages(reg, proto) != storage:
        entry["storage"] = storage  # Ghidra's order is not the convention's: the raw storage stays with each parameter
    return entry


def ask_model(cfg, prompt, system_file):
    """One no-tools session. Returns (answer text, cost, usage)."""
    from core import agent_cli, paths
    cmd = agent_cli.claude_command(dict(cfg, model=agent_cli.readable_model(cfg), effort="low", attempt_budget_usd=0.5),
                                   system_file)
    for flag in ("--tools", "--allowedTools"):
        if flag in cmd:
            cmd[cmd.index(flag) + 1] = ""
    rc, out, why = paths.run_bounded(cmd, 240, env=agent_cli.agent_env(dict(cfg, cache_ttl="5m")), stdin_text=prompt)
    final = {}
    for line in out.splitlines():
        if line.startswith("{") and '"result"' in line:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "result":
                final = d
    return final.get("result") or "", final.get("total_cost_usd") or 0.0, final.get("usage") or {}


def targets(system_list, scope, reg):
    from core import paths
    picked = {}
    for s in system_list:
        if scope and s["id"] not in scope:
            continue
        rows = [a for a in s["functions"] if a not in reg.functions and (paths.HARNESS / a / "meta.json").is_file()]
        if rows:
            picked[s["id"]] = rows
    return picked


def bottom_up(rows):
    """Callees first within a system: a function whose callees in rows are named goes before its callers."""
    calls = {a: set(callees(raw_of(a))) & set(rows) - {a} for a in rows}
    done, out = set(), []
    while len(out) < len(rows):
        ready = [a for a in rows if a not in done and calls[a] <= done]
        if not ready:  # a cycle: the one with the fewest callees left
            ready = [min((a for a in rows if a not in done), key=lambda a: len(calls[a] - done))]
        out.extend(ready)
        done.update(ready)
    return out


def write_names(sid, entries):
    from core import names_insert, readable
    path = readable.NAMES / f"{sid}.json"
    text = path.read_bytes().decode("utf-8") if path.is_file() else '{\n  "functions": {\n  },\n  "globals": {\n  }\n}\n'
    path.write_bytes(names_insert.insert(text, "functions", entries).encode("utf-8"))


def name_system(sid, rows, cfg, reg, used, lock, system_file, args):
    """Name one system's functions, batch by batch, each batch seeing the names before it. Returns its ledger rows."""
    from core import paths
    ledger, rows = [], bottom_up(rows)
    for i in range(0, len(rows), args.batch):
        batch, parts, metas = rows[i:i + args.batch], [], {}
        with lock:
            for a in batch:
                metas[a] = paths.read_json(paths.HARNESS / a / "meta.json")
                draft = pathlib.Path(args.drafts) / f"{a}.c" if args.drafts else None
                parts.append(describe(a, reg.proto(a, metas[a]), raw_of(a),
                                      draft.read_text(errors="replace") if draft and draft.is_file() else None, reg))
        prompt = "\n".join(parts)
        if args.dry_run:
            print(prompt[:3000])
            ledger.append({"system": sid, "functions": len(batch), "prompt_chars": len(prompt), "cost": 0.0})
            continue
        t0 = time.time()
        text, cost, usage = ask_model(cfg, prompt, system_file)
        got = parse_reply(text, set(batch))
        with lock:
            entries, bad = {}, []
            for a in batch:
                name, params = got.get(a, ("", []))
                if not valid_name(name):
                    bad.append(a)  # no usable name: the next run asks again, nothing is invented here
                    continue
                entries[a] = build_entry(reg, a, metas[a], name, params, used)
            write_names(sid, entries)
            reg.functions.update(entries)
        ledger.append({"system": sid, "functions": len(batch), "named": len(entries), "unnamed": bad, "cost": cost,
                       "model": cfg["readable_model"], "usage": usage, "seconds": round(time.time() - t0, 1)})
        print(f"{sid} batch {i // args.batch + 1}: {len(entries)}/{len(batch)} named, ${cost:.4f}", flush=True)
    return ledger


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--scope", default="", help="system ids, comma separated (default: every system)")
    ap.add_argument("--batch", type=int, default=30)
    ap.add_argument("--slots", type=int, default=4)
    ap.add_argument("--drafts", help="folder of lifted files ADDR.c (tools/readable_tx.py --out DIR, readable/ws)")
    ap.add_argument("--dry-run", action="store_true", help="print the prompts; no session runs and nothing is written")
    args = ap.parse_args()
    os.environ["DECOMP_PROJECT"] = args.project
    from core import paths, readable
    cfg = paths.config()
    reg = readable.Registry()
    used = {readable.parse_proto(e["proto"])[1] for e in reg.functions.values()}
    used |= {readable.global_name(e["decl"]) for e in reg.globals.values()}
    systems = paths.read_json(paths.STATE / "systems.json")["systems"]
    work = targets(systems, set(filter(None, args.scope.split(","))), reg)
    print(f"{sum(map(len, work.values()))} functions without a name in {len(work)} systems", flush=True)
    lock, ledger = threading.Lock(), []
    with tempfile.TemporaryDirectory() as tmp:
        system_file = pathlib.Path(tmp) / "system.txt"
        system_file.write_text(SYSTEM, encoding="utf-8")
        with cf.ThreadPoolExecutor(args.slots) as pool:
            jobs = [pool.submit(name_system, s, r, cfg, reg, used, lock, system_file, args) for s, r in work.items()]
            for j in jobs:
                ledger.extend(j.result())
    total = collections.Counter()
    for r in ledger:
        total["functions"] += r.get("named", 0)
        total["cost"] += r["cost"]
    print(f"named {total['functions']} functions for ${total['cost']:.4f}")
    if not args.dry_run:
        path = paths.STATE / "name_pass.json"
        old = paths.read_json(path) if path.is_file() else []
        path.write_text(json.dumps(old + ledger, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
