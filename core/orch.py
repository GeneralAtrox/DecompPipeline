"""Orchestrator: a pool of workers, each taking one function at a time from a shared queue.

Every function is judged in isolation (callees are mocked), so workers never depend on each other.
Shared resources: Ghidra (ghidra_parallel project slots, ghidra.slot), the ledger (self.lock),
the API account (rate limits back off without using up attempts) and money (budget_total_usd stops all).
"""
import collections
import os
import threading
import time
import traceback

from . import brief, filelock, job, paths, retire, systems, usage, watcher

ACTIVE = ("waiting", "preparing", "running")


def score(f):
    return (f["insns"] + 6 * f["cyclo"] + 40 * f["fpu_special"] + 30 * f["fp_mode"] + 30 * f["jump_tables"]
            + 25 * f["indirect_calls"] + 10 * f["callees"] + 3 * f["globals"])


def tier(f):
    if f["hw_refs"] or f["sys_ops"]:
        return "excluded"
    s = score(f)
    return "easy" if s < 80 else ("medium" if s < 250 else "hard")


class Worker:
    def __init__(self, orch, wid, addr=None):
        self.o, self.id, self.first = orch, wid, addr
        self.stop_flag = threading.Event()
        self.stop_reason = "stopped by user"
        self.current, self.system, self.cost, self.done = None, None, 0.0, 0
        self.thread = None

    def set_phase(self, addr, phase, attempt=None):
        with self.o.lock:
            same = self.current and self.current["addr"] == addr
            self.current = {"addr": addr, "phase": phase, "attempt": attempt, "since": time.time(),
                            "started": self.current["started"] if same else time.time()}

    def add_cost(self, usd):
        with self.o.lock:
            self.cost += usd

    def run(self):
        o = self.o
        try:
            addr = self.first
            while not self.stop_flag.is_set():
                target = o.claim(self, addr)
                addr = None
                if target is None:
                    o.log(f"[W{self.id}] queue is empty" + (
                        f"; not complete: {o.retire_pending} removed function(s) still have artefacts "
                        "(tools/retire.py)" if o.retire_pending else ""))
                    return
                self.cost = 0.0
                status = job.investigate(o, self, target)
                self.done += 1
                if self.stop_flag.is_set() or not o.ledger.get("loop"):
                    return
                o.log(f"[W{self.id}] loop: {target} ended {status}, continuing")
        except job.Fatal as e:
            o.log(f"STOPPING ALL WORKERS: {e}")
            o.stop_all(str(e))
        except Exception as e:  # noqa: BLE001 - surfaced in the UI
            o.log(f"[W{self.id}] ERROR " + str(e).splitlines()[0])
            (paths.STATE / "last_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            with o.lock:
                if self.current and o.ledger["functions"].get(self.current["addr"], {}).get("status") in ACTIVE:
                    o.ledger["functions"][self.current["addr"]]["status"] = "stopped"
                self.current = None
                o.workers.pop(self.id, None)
            o.save()
            o.log(f"[W{self.id}] finished ({self.done} function(s))")


class Orchestrator:
    def __init__(self):
        self.cfg = paths.config()
        self.lock = threading.RLock()
        self.log_lines = collections.deque(maxlen=600)
        self.workers, self.next_id = {}, 1
        self.usage = usage.Usage()
        catalog = paths.read_json(paths.CATALOG)
        if not catalog:
            raise RuntimeError(f"no catalog: run  python {paths.TOOL / 'decomp.py'} --project {paths.ROOT} setup  first")
        sc = paths.scope()  # the game ranges plus project.json "extra_functions" (core/scope.py)
        self.functions = {}
        for f in catalog["functions"]:
            if f["addr"] in sc:
                f["score"], f["tier"] = score(f), tier(f)
                self.functions[f["addr"]] = f
        self.extras = {a for a in self.functions if sc.is_extra(a)}
        self.sdk_count = len(catalog["functions"]) - len(self.functions)
        self.system_of, self.systems = systems.load()
        self.ledger = paths.read_json(paths.LEDGER, {"loop": False, "functions": {}})
        for rec in self.ledger["functions"].values():
            if rec.get("status") in ACTIVE:
                rec["status"] = "stopped"
        self.save()
        self.watcher = watcher.Watcher(self)
        self.retire_pending = self.removed_with_artefacts({f["addr"] for f in catalog["functions"]})

    def removed_with_artefacts(self, funcs):
        """How many addresses that are no longer functions still have artefacts (core/retire.py). The project is not
        complete until tools/retire.py moves them: the UI and the empty-queue message say so."""
        try:
            n = len({i["addr"] for i in retire.scan(paths.ROOT, funcs)["items"]})
        except Exception as e:  # noqa: BLE001 - a report, never a reason not to serve
            self.log(f"retire scan failed: {e}")
            return None
        if n:
            self.log(f"WARNING: {n} address(es) removed from the catalog still have artefacts (ledger rows, systems, "
                     f"harnesses, matched or readable C...): python tools/retire.py --project {paths.ROOT} --dry, "
                     "then without --dry, with the server stopped")
        return n

    # ---------- persistence ----------
    def save(self):
        """The server owns the ledger (tools that rewrite it refuse while it runs); the file lock still keeps an
        offline tool's locked read-modify-write and this write from interleaving."""
        with self.lock, filelock.locked(paths.LEDGER):
            paths.write_json(paths.LEDGER, self.ledger)

    def log(self, msg):
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        with self.lock:
            self.log_lines.append(line)
            with open(paths.STATE / "events.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def rec(self, addr):
        with self.lock:
            return self.ledger["functions"].setdefault(addr, {"addr": addr, "status": "queued", "attempts": []})

    def spent(self):
        with self.lock:
            return sum(a.get("cost_usd") or 0 for r in self.ledger["functions"].values() for a in r.get("attempts", []))

    def check_budget(self):
        cap = self.cfg.get("budget_total_usd")
        if cap and self.spent() >= cap:
            raise job.Fatal(f"total spend ${self.spent():.2f} reached budget_total_usd ${cap:.2f} (config.json)")

    # ---------- queue ----------
    def _open(self):
        """Eligible functions not yet taken, easiest first (priority list first)."""
        taken = {a for a, r in self.ledger["functions"].items() if r.get("status") not in (None, "queued", "stopped")}
        pri = [a for a in self.cfg["priority"] if a in self.functions and a not in taken]
        rest = sorted((f for a, f in self.functions.items() if a not in taken and f["tier"] != "excluded"
                       and a not in pri), key=lambda f: (f["score"], f["addr"]))
        return pri + [f["addr"] for f in rest]

    def claim(self, w, addr=None):
        """Atomically pick (and reserve) the next function for worker w.

        Preference: the priority list, then the worker's current system, then a system no other worker is in,
        then anything. Within each group, easiest first."""
        with self.lock:
            if addr is None:
                cands = self._open()
                if not cands:
                    return None
                busy = {x.system for x in self.workers.values() if x is not w and x.system}
                pri = [a for a in cands if a in self.cfg["priority"]]
                mine = [a for a in cands if w.system and self.system_of.get(a) == w.system]
                free = [a for a in cands if self.system_of.get(a) not in busy]
                addr = (pri or mine or free or cands)[0]
            w.system = self.system_of.get(addr)
            self.rec(addr)["status"] = "waiting"
            w.set_phase(addr, "claimed")
        self.save()
        return addr

    def queue_preview(self, n=8):
        return [{"addr": a, "tier": self.functions[a]["tier"], "score": self.functions[a]["score"],
                 "insns": self.functions[a]["insns"], "system": self.system_of.get(a)} for a in self._open()[:n]]

    # ---------- control ----------
    def busy(self):
        return bool(self.workers)

    def set_loop(self, enabled):
        with self.lock:
            self.ledger["loop"] = bool(enabled)
        self.save()

    def start(self, count=1, addr=None):
        """Start `count` new workers (or one worker on a specific address)."""
        cap = int(self.cfg.get("max_workers", 8))
        with self.lock:
            if addr:
                addr = paths.norm_addr(addr)
                if addr not in self.functions:
                    raise RuntimeError(f"{addr} is not a function to decompile (catalog, game ranges or "
                                       "extra_functions)")
                st = self.ledger["functions"].get(addr, {}).get("status")
                if st in ACTIVE:
                    raise RuntimeError(f"{addr} is already being worked on")
                count = 1
            count = max(0, min(int(count), cap - len(self.workers)))
            if count == 0:
                raise RuntimeError(f"already at the worker limit ({cap}, max_workers in config.json)")
            self.check_budget()
            for _ in range(count):
                w = Worker(self, self.next_id, addr)
                self.next_id += 1
                self.workers[w.id] = w
                w.thread = threading.Thread(target=w.run, daemon=True, name=f"worker-{w.id}")
                w.thread.start()
        self.log(f"started {count} worker(s)" + (f" on {addr}" if addr else ""))

    def requeue(self, addrs, note):
        """Put finished functions back in the queue (after a checker change). Active ones are left alone."""
        done = []
        with self.lock:
            for a in addrs:
                a = paths.norm_addr(a)
                r = self.ledger["functions"].get(a)
                if not r or r.get("status") in ACTIVE or r.get("status") == "queued":
                    continue
                r["requeued"] = f"{time.strftime('%Y-%m-%d %H:%M')}: was {r['status']}; {note}"
                r["status"] = "queued"
                r.pop("matched_file", None)
                m = paths.MATCHED / f"{a}.c"
                if m.exists():  # kept for reference; matched/ only holds C that passes the current checker
                    (paths.ROOT / "matched_stale").mkdir(exist_ok=True)
                    os.replace(m, paths.ROOT / "matched_stale" / f"{a}.c")
                done.append(a)
        self.save()
        self.log(f"requeued {len(done)} function(s): {note}")
        return done

    def stop_worker(self, wid):
        w = self.workers.get(int(wid))
        if w:
            w.stop_flag.set()
            self.log(f"[W{w.id}] stop requested")

    def stop_all(self, reason="stopped by user"):
        for w in list(self.workers.values()):
            w.stop_reason = reason
            w.stop_flag.set()
        self.log("stop requested for all workers")

    def run_one(self, addr):
        """Synchronous single investigation (CLI)."""
        w = Worker(self, 0)
        self.workers[0] = w
        try:
            return job.investigate(self, w, self.claim(w, addr))
        finally:
            self.workers.pop(0, None)

    # ---------- reporting ----------
    def overview(self):
        funcs = self.functions
        eligible = sum(1 for f in funcs.values() if f["tier"] != "excluded")
        # only rows of current functions count: a removed function's leftover row is not progress
        st = collections.Counter(r["status"] for a, r in self.ledger["functions"].items() if a in funcs)
        matched = [a for a, r in self.ledger["functions"].items() if r["status"] == "matched" and a in funcs]
        ex = self.extras  # counted in every total below, and apart here (project.json "extra_functions")
        extra = {"extra_functions": len(ex), "extra_eligible": sum(1 for a in ex if funcs[a]["tier"] != "excluded"),
                 "extra_matched": sum(1 for a in matched if a in ex)} if ex else {}
        return extra | {
            "game_functions": len(funcs) - len(ex), "eligible": eligible, "sdk_functions": self.sdk_count,
            "matched": st["matched"], "failed": st["failed"], "skipped": st["skipped"], "errors": st["error"],
            "remaining": eligible - st["matched"] - st["failed"] - st["skipped"] - st["error"],
            "bytes_matched": sum(funcs[a]["bytes"] for a in matched),
            "bytes_total": sum(f["bytes"] for f in funcs.values()),
            "tiers": dict(collections.Counter(f["tier"] for f in funcs.values())),
            "matched_by_tier": dict(collections.Counter(funcs[a]["tier"] for a in matched)),
            "cost_usd": round(self.spent(), 4), "budget_usd": self.cfg.get("budget_total_usd"),
            "retire_pending": self.retire_pending,
        }

    def state(self):
        attempt_keys = ("n", "verdict", "summary", "cost_usd", "duration_s", "tokens_in", "tokens_out", "turns",
                        "denials", "tamper", "killed", "final_message", "model", "effort", "thinking_tokens")
        with self.lock:
            hist = sorted(self.ledger["functions"].values(), key=lambda r: r.get("started", ""), reverse=True)
            workers = [{"id": w.id, "system": w.system, "cost": round(w.cost, 4), "done": w.done,
                        "stopping": w.stop_flag.is_set(), **(w.current or {})} for w in self.workers.values()]
            return {
                "project": self.cfg.get("name"), "overview": self.overview(), "loop": self.ledger.get("loop", False), "busy": self.busy(),
                "workers": workers, "usage": self.usage.view(), "watcher": self.watcher.view(), "queue": self.queue_preview(), "log": list(self.log_lines)[-150:],
                "systems": systems.summary(self.systems, self.functions, self.ledger["functions"]),
                "history": [{k: v for k, v in r.items() if k != "attempts"} | {
                    "attempt_count": len(r.get("attempts", [])),
                    "attempts": [{k: a.get(k) for k in attempt_keys} for a in r.get("attempts", [])],
                    "features": self.functions.get(r["addr"], {})} for r in hist[:80]],
                "config": {k: self.cfg.get(k) for k in ("model", "max_attempts", "stall_limit", "attempt_timeout_s",
                                                         "attempt_budget_usd", "vectors_dev", "vectors_hidden",
                                                         "workers", "max_workers", "budget_total_usd", "effort")},
            }

    def agent_prompt(self, addr):
        return brief.agent_prompt(self, addr)
