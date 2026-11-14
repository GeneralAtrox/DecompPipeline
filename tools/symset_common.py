"""Shared by the symbol-set inventories (targets/gamecube_ppc/tools/symbol_inventory.py, targets/xbox360_ppc/tools/pdb_public.py):
the machine gate and priority drop that heavy scans start with, the yes/no cell, the CSV and JSON writers, and the legend
lines for the content columns both public tables carry (what a set can actually give: function names, sizes, matching code,
library and object attribution, SDK functions, runtime functions, debug info, data symbols).

Nearest existing: tools/jobreg.py - it registers and refuses redundant jobs but does not check the machine is free; its
hook only blocks heavy commands on the main laptop. xbox360_ppc/tools/pdb_inventory.py has its own copy of ram_percent and
below_normal; it is left alone so the 445-archive scan it made stays reproducible.
"""
import csv
import ctypes
import json
import os
import pathlib
import subprocess

NOT_READ = "n/r"

CONTENT_LEGEND = [
    "**Fn names** `yes N`: N distinct function names across the set's maps and symbol files. **Sizes:** yes when the symbols carry a size "
    "(a procedure record in a PDB, a size column in a GameCube map or an ELF `st_size`); a linker map that gives only addresses reads no.",
    "**Code:** the set also holds the executable the symbols describe, so names can be matched to another build by bytes. **Lib/obj:** `lib+obj` "
    "the symbols say which library and which object each function came from; `obj` object names only; `no`.",
    "**SDK fns:** functions attributed to the console SDK's own libraries. **CRT:** functions from the C/C++ runtime libraries. "
    "**Data:** data symbols named. **Debug:** which of types, locals and line numbers the debug information holds.",
]


def below_normal():
    if os.name == "nt":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)


def gate():
    """Machine gate: no -benchmark process, at least 4 GB of RAM free, CPU under 85%. Returns (ok, why)."""
    if os.name != "nt":
        return True, ""

    class M(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong)] + \
                   [(f"f{i}", ctypes.c_ulonglong) for i in range(5)]
    m = M()
    m.l = ctypes.sizeof(M)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    if m.ap < 4 * 2**30:
        return False, f"only {m.ap / 2**30:.1f} GB RAM free"
    ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                         "(Get-CimInstance Win32_Process | ? { $_.CommandLine -match '-bench[m]ark' } | measure).Count; "
                         "(Get-CimInstance Win32_Processor | measure LoadPercentage -Average).Average"],
                        capture_output=True, text=True, timeout=60).stdout.split()
    if ps and ps[0] != "0":
        return False, "a -benchmark process is running"
    if len(ps) > 1 and float(ps[1]) >= 85:
        return False, f"CPU {ps[1]}%"
    return True, ""


def wait_gate(sleep_s=120):
    """The gate with one bounded retry; returns True when the machine is free."""
    import time
    ok, why = gate()
    if not ok:
        print(f"gate failed: {why}; waiting {sleep_s} s, then one retry", flush=True)
        time.sleep(sleep_s)
        ok, why = gate()
        if not ok:
            print(f"gate failed again: {why}")
    return ok


def yn(v, n=None):
    if v in (None, "not known"):
        return "not known"
    if not v:
        return "no"
    return f"yes {n:,}" if n is not None and not isinstance(n, bool) else "yes"


def write_rows(docs, stem, rows):
    """GAMECUBE-style flat rows (a list of dicts with identical keys) to <stem>.csv and <stem>.json."""
    docs = pathlib.Path(docs)
    docs.mkdir(parents=True, exist_ok=True)
    keys = list(rows[0])
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(docs / f"{stem}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    (docs / f"{stem}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
