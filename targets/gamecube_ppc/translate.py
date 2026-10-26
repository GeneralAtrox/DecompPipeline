"""DP's translator contract (docs/TRANSLATOR_STAGE.md) for ppc-translate, the GameCube PowerPC translator.

core/translate_stage runs this file with target.json "translator" "command": the translator itself is in
targets/gamecube_ppc/translator/ (unless config.local.json "translators" names another folder); this file only maps
what DP has for one function onto its API: the workspace (project.json's binary and fixed_regs, the function bodies
in state/function_bodies.txt, every harness's meta.json), the function's own meta.json (signature, parameter storage,
callees) and target.json (memory and ABI facts).

Where a callee returns its result (r3, f1 or nothing) comes from ppc-translate's whole-executable pass
(returns.analyse, DP's meta first). Its slow part, one summary per function of the code alone, is kept in
state/translator/ and built once per binary, catalog and translator code; the first call builds it (about 13-20 s on
Skies of Arcadia's 6751 functions) while other workers wait for it rather than building it too. The answer itself
(returns.cached_kinds) is kept there too, by those and the harness signatures: a call that finds it reads no
summaries and runs no rounds (2.2 s a function before, waste audit 2 C).

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
WAIT_S = 90  # how long a worker waits for another's summary build before building its own


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
    if returns.summaries_path(ws, cache).exists():  # built: analyse reads it (loading it here too cost 0.4 s)
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
    from ppctranslate import returns, workspace
    from ppctranslate.translate import Signature, TranslateError, translate_function
    ws = workspace.Workspace(a.workspace, target=a.target_json)
    addr = int(a.addr, 16)
    if addr not in ws.bodies:
        print(f"ppc-translate: {a.addr} has no body in state/function_bodies.txt", file=sys.stderr)
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
        print(f"ppc-translate: {e}", file=sys.stderr)
        return NO_TRANSLATION
    pathlib.Path(a.out).write_text(src, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
