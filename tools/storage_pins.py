"""Registry prototypes whose meaning changes under placement by storage (batch 14), and the "storage" pins that keep it.

Before batch 14, prototype parameter i went where the original's parameter i is (by position). Now the SH-4/SHC
convention places the parameters when their storages cover the original's as a set (core/readable_abi.py). A
prototype written in the raw signature's order with parameters of one class (Lib_Cache*(entry, cache): entry r5,
cache r4) passes both rules, with different meanings: by position its existing readable files and callers pass; by
storage they would fail. For each such names entry this tool prints the "storage" list that keeps the positional
meaning (or restore the natural order in the prototype and redo its readable files and callers instead).

Which meaning is meant comes from the readable files that judge the prototype (its own and its callers'), as
verified under the positional rule (state/readable.json before batch 14's verify):
  PIN      some of them are "readable": they pass by position, so the pin keeps them passing unchanged;
  NATURAL  all of them are "stale" (they failed by position): the prototype is in natural order, no pin;
  UNJUDGED no readable file judges it yet: pinned only with --pin-unjudged (else restore the natural order).

usage: python tools/storage_pins.py --project DIR [--ledger FILE] [--apply] [--pin-unjudged]
  --ledger  the readable ledger to take the evidence from (default: the project's state/readable.json; run this
            before `refine.py verify` rewrites it)
  --apply   writes the PIN entries into the project's readable/names/*.json (a post-deploy data step: "storage" is
            added to each entry, the rest of the file is kept as it is); without it, only prints
Also lists the prototypes the new placement refuses (with the reason), which need a pin or a new prototype.
"""
import argparse
import json
import os
import pathlib
import re
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))


def raw_storages(paths, readable_abi, reg, addr, records):
    """The original's parameter storages and names, in the raw signature's order: its own meta.json, else a callee
    record of a caller (the compared registers decide what Ghidra's names do not)."""
    meta = paths.read_json(paths.HARNESS / addr / "meta.json")
    if meta:
        raw = [p["storage"] if p["storage"] != "stack" else f"stack+{p['stack_offset']}" for p in meta["params"]]
        return raw, [p["name"] for p in meta["params"]], "meta"
    if addr in records:
        where, names = readable_abi.callee_where(reg, records[addr])
        return where, names, "callee record"
    return None, None, None


def add_storage(text, addr, raw):
    """The names file text with "storage" added to addr's entry, everything else as it was (one entry per line or
    not): inserted before the entry's closing brace."""
    m = re.search(rf'"(?:0x)?{addr}"\s*:\s*\{{', text, re.I)
    if not m:
        raise SystemExit(f"{addr}: entry not found")
    depth, i, in_str = 1, m.end(), False
    while depth:
        ch = text[i]
        if in_str:
            in_str = not (ch == '"' and text[i - 1] != "\\")
        elif ch == '"':
            in_str = True
        else:
            depth += {"{": 1, "}": -1}.get(ch, 0)
        i += 1
    end = i - 1
    body = text[:end].rstrip()
    return f'{body}, "storage": {json.dumps(raw)}{text[len(body):]}'


def positional_ok(reg, params, raw):
    """The rule before batch 14: by position, when the counts agree and no pointer comes from an FPU register."""
    return len(params) == len(raw) and all(not s.startswith("fr") or "*" not in t for (t, _), s in zip(params, raw))


def moves(reg, readable_abi, readable, proto, params, where, names, compared):
    """Whether a call record's compared arguments come from other parameters by position than by storage."""
    if not positional_ok(reg, params, where):
        return False  # the positional rule did not place this record either
    try:
        new = readable_abi.placement(reg, proto, where, names)
    except readable.RegistryError:
        return True
    old_at, new_at = {s: i for i, s in enumerate(where)}, {s: i for i, s in enumerate(new)}
    return any(old_at.get(s) != new_at.get(s) for s in compared)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--ledger")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--pin-unjudged", action="store_true")
    a = ap.parse_args()
    os.environ["DECOMP_PROJECT"] = str(pathlib.Path(a.project).resolve())
    from core import paths, readable, readable_abi
    reg = readable.Registry()
    led = json.loads(pathlib.Path(a.ledger or paths.STATE / "readable.json").read_text(encoding="utf-8"))
    led = led.get("functions", led)
    have = {p.stem for p in readable.SRC.glob("8c*.c")}
    records, judges = {}, {}
    for d in sorted(paths.HARNESS.iterdir()) if paths.HARNESS.exists() else []:
        m = paths.read_json(d / "meta.json")
        for c in (m or {}).get("callees", []):
            if c["addr"] in reg.functions:
                records.setdefault(c["addr"], c)
                if d.name in have:
                    judges.setdefault(c["addr"], {})[d.name] = c
    pins, refused = {}, []
    for addr, e in sorted(reg.functions.items()):
        proto = e["proto"]
        try:
            params = readable.parse_proto(proto)[2]
            raw, names, src = raw_storages(paths, readable_abi, reg, addr, records)
            if raw is None:
                continue
            new = readable_abi.placement(reg, proto, raw, names, readable_abi.override(reg, addr, proto))
        except readable.RegistryError as err:
            refused.append((addr, str(err)))
            continue
        if "storage" in e or not positional_ok(reg, params, raw) or new == raw:
            continue
        # the files whose compared arguments the change moves: the function's own, and callers by their records
        files = [addr] if addr in have else []
        own = paths.read_json(paths.HARNESS / addr / "meta.json") or {}
        for caller, rec in sorted(judges.get(addr, {}).items()):
            storage = readable_abi.param_storage(own) if own.get("signature") == rec["signature"] else None
            where, wnames = readable_abi.callee_where(reg, rec, storage)
            compared = {s for s in map(readable_abi.arg_storage, rec["regs"]) if s}
            if moves(reg, readable_abi, readable, proto, params, where, wnames, compared):
                files.append(caller)
        status = {f: (led.get(f) or {}).get("status") for f in files}
        kind = "UNJUDGED" if not files else "PIN" if "readable" in status.values() else "NATURAL"
        if kind == "PIN" or (kind == "UNJUDGED" and a.pin_unjudged):
            pins[addr] = raw
        print(f"{kind} {addr} ({reg.where[addr]}, from its {src}): {proto}\n    by position {raw}; by storage "
              f"{new}; files: {' '.join(f'{f}={status[f]}' for f in files) or 'none'}")
    for addr, why in refused:
        print(f"REFUSED {addr}: {why}")
    print(f"{len(pins)} prototype(s) to pin, {len(refused)} refused")
    if a.apply and pins:
        by_file = {}
        for addr, raw in pins.items():
            by_file.setdefault(reg.where[addr], {})[addr] = raw
        for fname, items in by_file.items():
            f = readable.NAMES / fname
            text = f.read_text(encoding="utf-8")
            for addr, raw in items.items():
                text = add_storage(text, addr, raw)
            data = json.loads(text)  # still valid JSON, with the pins
            assert all(data["functions"][k].get("storage") for k in data["functions"] if paths.norm_addr(k) in items)
            f.write_text(text, encoding="utf-8")
            print(f"wrote {len(items)} pin(s) to {f}")


if __name__ == "__main__":
    main()
