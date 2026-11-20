"""Xbox 360 port kit: translate --all (and --again) across worker processes.

Each worker holds its own Kit and writes gen/fn_ADDR.c itself (Kit.translate: a temp file, then a rename). It returns its answer, and
only the parent writes state.json, so no worker overwrites another's record. A stopped pass carries on: what is in gen/ is skipped.
out/translate_times.tsv gets one row per function: seconds, address, size in bytes, the answer.
Nearest existing: Kit.want, one process, which took about 4 s a function on the big ones at the end of Too Human (14 hours for the
last 13,000); tools/readable_tx.py's pool runs model calls and has no per-process Kit.
"""
import concurrent.futures
import time

_kit = None


def _init(ws, out, game):
    global _kit
    import gen_xbox
    gen_xbox.idle_priority()
    _kit = gen_xbox.Kit(ws, out, game)
    _kit.open()


def _one(addr):
    t0 = time.time()
    return addr, _kit.translate(addr), time.time() - t0


def translate_all(kit, jobs, todo=None):
    """Every catalogue function outside the replaced ranges and not yet in gen/; or the functions in todo (translate --again: those in gen/
    whose C shows a translator change's old form, which one process took over 90 minutes for in Too Human)."""
    kit.open()                      # builds the analyses' caches once, before any worker reads them
    if todo is None:
        have = kit.have()
        todo = [a for a in sorted(kit.ws.bodies) if a not in have and not kit.replaced(a)]
    size = {a: f.get("size", 0) for a, f in kit.ws.catalog.items()}
    print(f"{len(todo)} to translate, {jobs} workers", flush=True)
    t0 = last = time.time()
    written = failed = 0
    with open(kit.out / "translate_times.tsv", "a", encoding="utf-8") as times, \
            concurrent.futures.ProcessPoolExecutor(jobs, initializer=_init, initargs=(str(kit.wsdir), str(kit.out), kit.game.name)) as pool:
        try:
            for fut in concurrent.futures.as_completed([pool.submit(_one, a) for a in todo]):
                addr, why, secs = fut.result()
                times.write(f"{secs:.1f}\t{addr:08x}\t{size.get(addr, 0)}\t{why or 'written'}\n")
                if why is None:
                    written += 1
                else:
                    failed += 1
                    kit.state["failed"][f"{addr:08x}"] = why
                if time.time() - last > 60:
                    last = time.time()
                    times.flush()
                    kit.save()
                    print(f"{written + failed} of {len(todo)}: {written} written, {failed} not, {last - t0:.0f} s", flush=True)
        finally:
            kit.save()
    print(f"{written + failed} of {len(todo)}: {written} written, {failed} not, {time.time() - t0:.0f} s", flush=True)
