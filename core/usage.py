"""Subscription usage (5-hour and weekly windows) for the account the claude CLI is logged into.

Every CLI session prints a stream-json `rate_limit_event` with the account's utilisation per window and when
it resets. We keep the latest one (from real attempts, or from a one-line probe session) in state/usage.json.
No credentials are read: the numbers come from the CLI's own output.
"""
import json
import threading
import time

from . import filelock, paths

USAGE = paths.STATE / "usage.json"
PROBE_DIR = paths.STATE / "usage_probe"
PROBE_TIMEOUT_S = 90
WINDOW_NAMES = {"five_hour": "5-hour", "seven_day": "weekly", "seven_day_opus": "weekly (Opus)",
                "seven_day_sonnet": "weekly (Sonnet)"}


class Usage:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = paths.read_json(USAGE, {})
        self.probing = False
        self.probe_error = None
        self.last_probe = 0.0

    def update(self, info, source):
        """info: the rate_limit_info object of a rate_limit_event."""
        if not isinstance(info, dict):
            return
        windows = {}
        for key, w in (info.get("unifiedWindows") or {}).items():
            if isinstance(w, dict):
                windows[key] = {"utilization": w.get("utilization"), "resets_at": w.get("resetsAt")}
        with self.lock, filelock.locked(USAGE):  # the server and refine runs both record readings
            if not windows:  # keep the windows of the latest reading, whichever process recorded it
                windows = (paths.read_json(USAGE, {}) or self.data).get("windows") or self.data.get("windows", {})
            self.data = {"status": info.get("status"), "limiting": info.get("rateLimitType"),
                         "resets_at": info.get("resetsAt"), "windows": windows,
                         "updated": time.time(), "source": source}
            paths.write_json(USAGE, self.data)

    def blocked_until(self):
        """Epoch seconds when a hit limit resets, or None while sessions are allowed."""
        with self.lock:
            d = dict(self.data)
        if d.get("status") != "rejected":
            return None
        until = d.get("resets_at") or 0
        return until if until > time.time() else None

    def view(self):
        with self.lock:
            d = dict(self.data)
        out = []
        for key, w in d.get("windows", {}).items():
            u = w.get("utilization")
            out.append({"key": key, "name": WINDOW_NAMES.get(key, key), "resets_at": w.get("resets_at"),
                        "percent": None if u is None else round(u * 100 if u <= 1 else u, 1)})
        return {"windows": out, "status": d.get("status"), "limiting": d.get("limiting"),
                "updated": d.get("updated"), "source": d.get("source"), "probing": self.probing,
                "probe_error": self.probe_error, "blocked_until": self.blocked_until()}

    def age_s(self):
        """Seconds since the last reading or probe attempt (so a failing probe is not retried every poll)."""
        with self.lock:
            return time.time() - max(self.data.get("updated") or 0, self.last_probe)

    def probe_async(self, cfg, log):
        """Run a one-line Haiku session in the background to read the current numbers."""
        with self.lock:
            if self.probing:
                return False
            self.probing, self.last_probe = True, time.time()
        threading.Thread(target=self._probe, args=(cfg, log), daemon=True, name="usage-probe").start()
        return True

    def _probe(self, cfg, log):
        found = {}

        def on_line(line):
            try:
                ev = json.loads(line)
            except ValueError:
                return
            if ev.get("type") == "rate_limit_event":
                found["info"] = ev.get("rate_limit_info")

        try:
            PROBE_DIR.mkdir(parents=True, exist_ok=True)
            cmd = [paths.resolve_exe(cfg["claude"]), "-p", "--model", "haiku", "--output-format", "stream-json", "--verbose",
                   "--no-session-persistence", "--tools", "", "--max-budget-usd", "0.05"]
            rc, out, reason = paths.run_bounded(cmd, PROBE_TIMEOUT_S, cwd=PROBE_DIR,
                                                stdin_text="Reply with just: ok", on_line=on_line)
            if found.get("info"):
                self.update(found["info"], "probe")
                self.probe_error = None
                log("usage refreshed (one-line probe session)")
            else:
                self.probe_error = reason or f"no usage in CLI output (exit {rc}): " + (out or "")[-200:].strip()
                log("usage probe failed: " + self.probe_error)
        except Exception as e:  # noqa: BLE001 - shown in the UI
            self.probe_error = str(e).splitlines()[0]
            log("usage probe failed: " + self.probe_error)
        finally:
            self.probing = False
