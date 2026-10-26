"""DP's translator contract (docs/TRANSLATOR_STAGE.md) for xenon-translate, the Xbox 360 (Xenon PowerPC) translator.

core/translate_stage runs this file with target.json "translator" "command": the translator itself is in
targets/xbox360_ppc/translator/ (unless config.local.json "translators" names another folder); this file only maps
what DP has for one function onto its API: the workspace (project.json's binary and fixed_regs, image.json's
sections, the function bodies and frame-helper ranges in catalog_inputs.json, every harness's meta.json), the
function's own meta.json (signature, parameter storage, callees) and target.json (memory and ABI facts).

Where a callee returns its result (r3, f1 or nothing) comes from xenon-translate's whole-executable pass
(returns.analyse, DP's meta first). Its slow part, one summary per function of the code alone, is kept in
state/translator/ and built once per executable and catalog; the first call builds it (about 2 minutes on Too
Human's 88,187 functions) while other workers wait for it rather than building it too. The answer itself
(returns.cached_kinds) is kept there too, by those, the translator's code and the harness signatures: a call that
finds it reads no summaries and runs no rounds (11.7 s a function before, waste audit 2 row 19).

usage: python translate.py --repo DIR --workspace DIR --addr HEX --meta FILE --target-json FILE --out FILE
Exit 0: --out holds the C. Exit 3: no translation (the reason on stderr). Anything else: the translator crashed.
"""
import argparse
import json
import os
import pathlib
import sys
import time

NO_TRANSLATION = 3
WAIT_S = 300  # how long a worker waits for another's summary build before building its own
MAX_C = 60000  # core/lint's work.c cap: larger C would only fail the lint


def summaries_ready(ws, cache, returns):
    """Build the summary cache once: one worker holds state/translator/building.lock, the others wait for it (a lock
    older than WAIT_S is a killed worker's and is taken over)."""
    cache.mkdir(parents=True, exist_ok=True)
    lock = cache / "building.lock"
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > WAIT_S:
            lock.unlink()
    except OSError:
        pass
    if returns.summaries_path(ws, cache).exists():  # built: analyse reads it (loading it here too cost 3.3 s)
        return
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        t0 = time.monotonic()
        while lock.exists() and time.monotonic() - t0 < WAIT_S:
            time.sleep(0.5)
        return
    try:
        os.close(fd)
        returns.code_summaries(ws, cache)
    finally:
        lock.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    for k in ("--repo", "--workspace", "--addr", "--meta", "--target-json", "--out"):
        ap.add_argument(k, required=True)
    a = ap.parse_args()
    sys.path.insert(0, a.repo)
    from xenontranslate import returns, workspace
    from xenontranslate.translate import Signature, TranslateError, translate_function
    ws = workspace.Workspace(a.workspace, target=a.target_json)
    addr = int(a.addr, 16)
    if addr not in ws.bodies:
        print(f"xenon-translate: {a.addr} is not a function of catalog_inputs.json", file=sys.stderr)
        return NO_TRANSLATION
    meta = json.loads(pathlib.Path(a.meta).read_text(encoding="utf-8"))
    cache = pathlib.Path(a.workspace) / "state" / "translator"
    kinds = returns.cached_kinds(ws, cache)  # the answer for these inputs: no summaries, no rounds, no lock
    if kinds is None:
        summaries_ready(ws, cache, returns)
        kinds = returns.analyse(ws, cache)
    try:
        src, _ = translate_function(ws.dol, addr, Signature.from_meta(meta), ws.facts, ws.bodies[addr],
                                    returns=kinds)
    except TranslateError as e:
        print(f"xenon-translate: {e}", file=sys.stderr)
        return NO_TRANSLATION
    if len(src) > MAX_C:
        print(f"xenon-translate: the C is {len(src)} characters, over the {MAX_C} cap", file=sys.stderr)
        return NO_TRANSLATION
    pathlib.Path(a.out).write_text(src, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
