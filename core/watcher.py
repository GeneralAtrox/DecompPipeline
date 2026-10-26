"""The watcher: one Opus session at a time repairs the oracle's input generator for functions that were
skipped because the oracle could not produce enough valid test vectors.

Woken by job.finish (a new oracle skip) and by a sweep every watcher_sweep_s seconds. The agent works in
watcher/ws/ and may edit only TcInputs.java and hints/<addr>.txt; it runs checks only through wtool.py,
which asks this server to run them. Every decision is made here by gate.py, never by the agent.
"""
import collections
import json
import os
import secrets
import shutil
import stat
import sys
import threading
import time

from . import agent, gate, ghidra, paths, watch_brief

SKIP_PREFIX = "oracle could not produce"
WS = gate.WATCH / "ws"
HISTORY = gate.WATCH / "history.json"


def defaults(cfg):
    return {"model": cfg.get("watcher_model") or "claude-opus-5-5", "effort": cfg.get("watcher_effort"),
            "timeout": int(cfg.get("watcher_timeout_s", 3600)), "budget": cfg.get("watcher_budget_usd", 15.0),
            "max_tries": int(cfg.get("watcher_max_tries", 2)), "sweep": int(cfg.get("watcher_sweep_s", 60)),
            "max_trials": int(cfg.get("watcher_max_trials", 12)), "max_gates": int(cfg.get("watcher_max_gates", 3))}


class Watcher:
    def __init__(self, o):
        self.o = o
        self.lock = threading.Lock()
        self.events = collections.deque()
        self.thread = None
        self.session_stop = threading.Event()
        self.current = None      # {"addr", "phase", "since"}
        self.token = None
        self.req = None          # the running/finished tool request of this session
        self.counts = {}         # tool requests used this session
        self.promoted = None     # promote() result once the gate passed this session
        self.last_report = None  # the last gate/try result this session
        self.last_sweep = 0.0
        self.last_sweep_info = None  # {"time", "eligible", "out_of_scope": {reason: count}}
        self.history = paths.read_json(HISTORY, [])
        if self.enabled():
            self.start()

    # ---------- control ----------
    def enabled(self):
        return bool(self.o.ledger.get("watcher"))

    def set_enabled(self, on):
        with self.o.lock:
            self.o.ledger["watcher"] = bool(on)
        self.o.save()
        self.o.log(f"[WATCH] watcher {'ON' if on else 'OFF'}")
        if on:
            self.start()
        else:
            self.session_stop.set()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.last_sweep = 0.0
            self.thread = threading.Thread(target=self.run, daemon=True, name="watcher")
            self.thread.start()

    def notify(self, addr):
        """Called by job.finish for a new oracle skip: wake the watcher now instead of at the next sweep."""
        self.events.append(addr)

    # ---------- queue ----------
    def eligible(self, addr):
        r = self.o.ledger["functions"].get(addr) or {}
        return (r.get("status") == "skipped" and (r.get("verdict_detail") or "").startswith(SKIP_PREFIX)
                and r.get("watcher_tries", 0) < defaults(self.o.cfg)["max_tries"])

    def candidates(self):
        with self.o.lock:
            return sorted(a for a in self.o.ledger["functions"] if self.eligible(a))

    def out_of_scope(self):
        """Skips the watcher cannot repair (inputs only), counted by reason, plus those out of tries."""
        reasons = collections.Counter()
        with self.o.lock:
            for a, r in self.o.ledger["functions"].items():
                if r.get("status") != "skipped" or self.eligible(a):
                    continue
                d = r.get("verdict_detail") or ""
                reasons["out of watcher tries" if d.startswith(SKIP_PREFIX) else d.split(" param_")[0]] += 1
        return dict(reasons.most_common())

    def sweep(self):
        c, skipped = self.candidates(), self.out_of_scope()
        info = {"eligible": len(c), "out_of_scope": skipped}
        if info != {k: (self.last_sweep_info or {}).get(k) for k in info}:  # log changes, not every minute
            other = ", ".join(f"{k} x{v}" for k, v in skipped.items()) or "none"
            self.o.log(f"[WATCH] sweep: {len(c)} oracle skip(s) to repair; skipped for other reasons: {other}")
        self.last_sweep_info = dict(info, time=time.time())
        return c[0] if c else None

    def next_target(self):
        while self.events:
            a = self.events.popleft()
            if self.eligible(a):
                return a
        if time.time() - self.last_sweep >= defaults(self.o.cfg)["sweep"]:
            self.last_sweep = time.time()
            return self.sweep()
        return None

    def run(self):
        try:
            while self.enabled():
                addr = self.next_target()
                if addr is None:
                    time.sleep(1.0)
                    continue
                self.session_stop = threading.Event()  # per session: a late timer cannot stop the next one
                try:
                    self.handle(addr)
                except Exception as e:  # noqa: BLE001 - surfaced in the log and history
                    self.o.log(f"[WATCH] {addr}: ERROR {str(e).splitlines()[0][:200]}")
                    self.record(addr, "error", str(e).splitlines()[0][:300], {})
                finally:
                    self.current, self.token = None, None
        finally:
            self.o.log("[WATCH] watcher thread stopped")

    def phase(self, addr, text):
        self.current = {"addr": addr, "phase": text, "since": time.time()}

    # ---------- one session ----------
    def build_workspace(self, addr):
        if WS.exists():
            for p in WS.rglob("*"):
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            shutil.rmtree(WS)
        (WS / "target").mkdir(parents=True)
        (WS / "hints").mkdir()
        shutil.copyfile(paths.INPUTS, WS / "TcInputs.java")
        old_hints = paths.HINTS / f"{addr}.txt"
        (WS / "hints" / f"{addr}.txt").write_text(
            old_hints.read_text(encoding="utf-8") if old_hints.exists() else f"# hints for fn_{addr}\n", encoding="utf-8")
        scripts = paths.target_scripts()
        for n in gate.locked():
            shutil.copyfile(scripts[n], WS / n)
        for p in ghidra.packet_files(paths.PACKETS / addr):
            shutil.copyfile(p, WS / "target" / p.name)
        summary = paths.read_json(paths.HARNESS / addr / "oracle_summary.json") or {}
        for n in ("meta.json", "oracle_summary.json"):
            if (paths.HARNESS / addr / n).exists():
                shutil.copyfile(paths.HARNESS / addr / n, WS / "target" / n)
        shutil.copyfile(paths.TOOLS / "wtool.py", WS / "wtool.py")
        self.token = secrets.token_hex(16)
        (WS / ".watch").write_text(json.dumps({"port": self.o.cfg["port"], "token": self.token}), encoding="utf-8")
        task = watch_brief.watch_md(self.o.cfg, addr, summary)
        (WS / "WATCH.md").write_text(task, encoding="utf-8")
        for p in WS.rglob("*"):
            if p.is_file() and p.name not in ("TcInputs.java", f"{addr}.txt"):
                os.chmod(p, stat.S_IREAD)
        return task

    def command(self):
        d = defaults(self.o.cfg)
        override = os.environ.get("TCD_WATCHER_COMMAND")  # JSON list; dry runs with tools/fake_watcher.py
        if override:
            return [sys.executable if c == "{python}" else c.replace("{root}", str(paths.TOOL))
                    for c in json.loads(override)]
        return [
            paths.resolve_exe(self.o.cfg["claude"]), "-p", "--model", d["model"],
            "--output-format", "stream-json", "--verbose",
            "--safe-mode", "--restricted", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
            "--permission-mode", "dontAsk",
            "--tools", "Read,Edit,Write,Bash",
            "--allowedTools", "Read,Edit(./TcInputs.java),Write(./TcInputs.java),Edit(./hints/*.txt),"
                              "Bash(python wtool.py try),Bash(python wtool.py gate),Bash(python wtool.py wait)",
            "--max-budget-usd", str(d["budget"]),
            *(["--effort", d["effort"]] if d["effort"] else []),
        ]

    def handle(self, addr):
        o, d = self.o, defaults(self.o.cfg)
        while o.usage.blocked_until() and self.enabled():
            self.phase(addr, "waiting for the account's usage limit to reset")
            time.sleep(5.0)
        if not self.enabled():
            return
        with o.lock:
            r = o.ledger["functions"][addr]
            r["watcher_tries"] = r.get("watcher_tries", 0) + 1
            n = r["watcher_tries"]
        o.save()
        self.req, self.counts, self.promoted, self.last_report = None, {}, None, None
        self.phase(addr, f"session {n}/{d['max_tries']}: building workspace")
        task = self.build_workspace(addr)
        sessions = gate.WATCH / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        transcript = sessions / f"{time.strftime('%Y%m%d-%H%M%S')}_{addr}.jsonl"
        o.log(f"[WATCH] {addr}: session {n}/{d['max_tries']} with {d['model']} (transcript {paths.show(transcript)})")
        self.phase(addr, f"session {n}/{d['max_tries']}: {d['model']} is working")
        final, t0 = {}, time.time()
        with open(transcript, "w", encoding="utf-8") as tf:
            def on_line(line):
                tf.write(line)
                tf.flush()
                try:
                    ev = json.loads(line)
                except ValueError:
                    return
                if ev.get("type") == "result":
                    final.update(ev)
                elif ev.get("type") == "rate_limit_event":
                    o.usage.update(ev.get("rate_limit_info"), "watcher")
                msg = agent.describe_event(ev)
                if msg:
                    o.log(f"[WATCH] {addr}: {msg}")

            rc, out, reason = paths.run_bounded(self.command(), d["timeout"], cwd=WS,
                                                stdin_text="Follow WATCH.md (reproduced below) exactly.\n\n" + task,
                                                on_line=on_line, stop_flag=self.session_stop)
        self.wait_request(1800)  # a gate may still be finishing after the session ended
        info = {"cost_usd": final.get("total_cost_usd"), "duration_s": round(time.time() - t0, 1),
                "turns": final.get("num_turns"), "final_message": (final.get("result") or "")[-300:],
                "killed": reason if not self.promoted else None, "requests": dict(self.counts),
                "denials": len(final.get("permission_denials") or []), "transcript": transcript.name,
                "api_error": agent.api_error(final, out)}
        if self.promoted:
            with o.lock:
                r = o.ledger["functions"][addr]
                r["status"], r["verdict_detail"] = "queued", "requeued after the watcher improved the oracle"
            o.save()
            self.record(addr, "promoted", "gate passed; requeued", info)
        elif not self.enabled():
            with o.lock:  # switched off by the user: the session does not count as a try
                o.ledger["functions"][addr]["watcher_tries"] -= 1
            o.save()
            self.record(addr, "stopped", "watcher switched off during the session", info)
        else:
            why = "gate never passed"
            if self.last_report:
                bad = [f"{c[0]}: {c[2]}" for c in self.last_report["checks"] if not c[1]]
                why = "last check failed: " + (bad[0] if bad else "?")
            self.record(addr, "rejected", why, info)

    def record(self, addr, outcome, detail, info):
        e = {"addr": addr, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "outcome": outcome, "detail": detail,
             **info, "checks": (self.last_report or {}).get("checks", []),
             "archive": (self.promoted or {}).get("archive"), "diff": (self.promoted or {}).get("diff", ""),
             "hints": (self.promoted or {}).get("hints", "")}
        self.history.append(e)
        self.history = self.history[-200:]
        gate.WATCH.mkdir(parents=True, exist_ok=True)
        paths.write_json(HISTORY, self.history)
        self.o.log(f"[WATCH] {addr}: {outcome.upper()} - {detail}")

    # ---------- the agent's tool requests (wtool.py -> server -> here) ----------
    def tool(self, token, kind):
        if not self.current or not token or not secrets.compare_digest(str(token), self.token or ""):
            raise PermissionError("no watcher session with this token")
        d = defaults(self.o.cfg)
        with self.lock:
            busy = self.req and not self.req["done"].is_set()
            if kind in ("try", "gate") and not busy:
                limit = d["max_trials"] if kind == "try" else d["max_gates"]
                if self.promoted:
                    return {"done": True, "text": "Already promoted in this session. Your work is done."}
                if self.counts.get(kind, 0) >= limit:
                    return {"done": True, "text": f"REFUSED: the limit of {limit} '{kind}' runs per session is used up."}
                self.counts[kind] = self.counts.get(kind, 0) + 1
                self.req = {"kind": kind, "done": threading.Event(), "text": ""}
                threading.Thread(target=self.serve_request, args=(self.current["addr"], self.req), daemon=True).start()
            elif kind not in ("try", "gate", "wait"):
                raise ValueError("kind must be try, gate or wait")
            req = self.req
        if req is None:
            return {"done": True, "text": "Nothing is running. Use: python wtool.py try"}
        req["done"].wait(25)
        prefix = "" if kind == "wait" or kind == req["kind"] else f"(the earlier '{req['kind']}' is still the active request)\n"
        return {"done": req["done"].is_set(), "text": prefix + (req["text"] or f"{req['kind']} is running...")}

    def serve_request(self, addr, req):
        o, kind, stop = self.o, req["kind"], self.session_stop
        try:
            self.phase(addr, f"agent requested '{kind}': running server-side checks")
            o.log(f"[WATCH] {addr}: {kind} requested")
            res = gate.run_gate(o.cfg, o, addr, WS, stop_flag=stop, log=o.log, full=(kind == "gate"))
            self.last_report = res
            if kind == "gate" and res["ok"]:
                self.promoted = gate.promote(o.cfg, addr, res["snapshot"], res, o.log)
                res = dict(res, promoted=True, archive=self.promoted["archive"])
                threading.Timer(60.0, stop.set).start()  # the session has nothing left to do
            passed = sum(1 for c in res["checks"] if c[1])
            o.log(f"[WATCH] {addr}: {kind} -> {passed}/{len(res['checks'])} checks passed"
                  + (" - PROMOTED" if res.get("promoted") else ""))
            req["text"] = watch_brief.format_result(kind, res)
        except Exception as e:  # noqa: BLE001
            req["text"] = f"{kind} failed to run: {str(e).splitlines()[0][:300]}"
            o.log(f"[WATCH] {addr}: {kind} error: {str(e).splitlines()[0][:200]}")
        finally:
            req["done"].set()
            if self.current:
                self.phase(addr, "agent is working")

    def wait_request(self, timeout):
        req = self.req
        if req:
            req["done"].wait(timeout)

    # ---------- UI ----------
    def view(self):
        d = defaults(self.o.cfg)
        return {"enabled": self.enabled(), "model": d["model"], "effort": d["effort"] or "CLI default",
                "max_tries": d["max_tries"], "current": self.current, "sweep": self.last_sweep_info,
                "queue": self.candidates()[:20], "history": [dict(h, diff=(h.get("diff") or "")[:6000])
                                                             for h in self.history[-12:]][::-1]}
