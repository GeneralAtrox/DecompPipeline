"""The Ghidra broker's one-JVM peak expires, and the broker says when it holds its slots far below the configured number
(core/ghidra_peak.py; core/ghidra_broker.cap). The case: on build box A (16 GB) one 5,990 MB outlier (normal JVMs 150-400 MB)
stayed in jvm_peak.json for good, by_ram() sized every JVM by it, and 8 slots ran as about 1.
1. a stale outlier (older than peak_ttl_s) is dropped: the peak is the next JVM measured, by_ram() allows 8 again;
2. a fresh one still counts; an old one counts while a JVM near it is running (its record is refreshed); peak_ttl_s is config;
   a record with no "at" (written before this) ages by its file's time;
3. throttle_note() speaks only when the broker allows at most half of the configured JVMs and two fewer;
4. through cap(): a fresh outlier leaves 1 of 8 and the note is in ram_log.jsonl and on stderr once; the stale one leaves 8, no note.
Run: python -W error tests/test_ghidra_peak.py
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)
from test_ghidra_broker import Machine, cfg  # noqa: E402

from core import ghidra_broker, ghidra_peak  # noqa: E402

HOUR = 3600.0


def folder():
    return pathlib.Path(tempfile.mkdtemp(prefix="peak", dir=TMP))


def outlier(d, ago_s, mb=5990.0, at=True):
    f = d / "jvm_peak.json"
    f.write_text(json.dumps({"mb": mb, "pid": 1, **({"at": time.time() - ago_s} if at else {})}), encoding="utf-8")
    if not at:
        t = time.time() - ago_s
        os.utime(f, (t, t))
    return f


class Expiry(unittest.TestCase):
    def b(self, **kw):
        return {**ghidra_broker.settings(cfg(2, by_ram=True, max_jvms=8, **kw))[0]}

    def test_a_stale_outlier_stops_counting(self):  # Build box A: 16,000 MB, RAM 40%
        b, d = self.b(), folder()
        f = outlier(d, 10 * HOUR)
        self.assertEqual(ghidra_broker.by_ram(b, 40.0, 16000.0, [], 0, 5990.0), 1)  # what the old peak left
        self.assertEqual(ghidra_peak.peak(b, d, {11: 300.0}), 300.0)  # expired; the JVM running now is the peak
        self.assertEqual(json.loads(f.read_text(encoding="utf-8"))["mb"], 300.0)
        self.assertEqual(ghidra_broker.by_ram(b, 40.0, 16000.0, [300.0], 0, 300.0), 8)
        outlier(d, 10 * HOUR)
        self.assertEqual(ghidra_peak.peak(b, d, {}), 1024.0)  # nothing running: peak_mb, and the record is gone
        self.assertFalse(f.exists())

    def test_fresh_alive_and_configured(self):
        b, d = self.b(), folder()
        outlier(d, 0.5 * HOUR)
        self.assertEqual(ghidra_peak.peak(b, d, {11: 300.0}), 5990.0)  # within the hour: still counts
        f = outlier(d, 10 * HOUR)
        self.assertEqual(ghidra_peak.peak(b, d, {7: 5900.0, 8: 200.0}), 5990.0)  # a JVM near it is alive: kept ...
        self.assertLess(time.time() - json.loads(f.read_text(encoding="utf-8"))["at"], 5)  # ... and refreshed
        outlier(d, 600.0)
        self.assertEqual(ghidra_peak.peak(self.b(peak_ttl_s=60), d, {11: 300.0}), 300.0)  # peak_ttl_s is config
        outlier(d, 10 * HOUR, mb=960.0, at=False)  # a record from before "at": its file's time
        self.assertEqual(ghidra_peak.peak(b, d, {11: 300.0}), 300.0)
        self.assertEqual(ghidra_peak.peak(b, d, {12: 500.0}), 500.0)  # raised as before
        self.assertEqual(ghidra_peak.peak(b, d, {13: 400.0}), 500.0)

    def test_note(self):
        b = self.b()
        note = ghidra_peak.throttle_note(b, 1, 8, 5990.0, 16000.0, 40.0)
        self.assertIn("1 of 8", note)
        self.assertIn("5990 MB", note)
        self.assertEqual(ghidra_peak.throttle_note(b, 5, 8, 5990.0, 16000.0, 40.0), "")
        self.assertEqual(ghidra_peak.throttle_note(b, 1, 2, 5990.0, 16000.0, 40.0), "")  # two slots: not "far below"
        self.assertEqual(ghidra_peak.ceiling(b, 40.0, cores=24), 8)
        self.assertEqual(ghidra_peak.ceiling({**b, "max_jvms": 0}, 90.0, cores=24), 6)


class ThroughCap(unittest.TestCase):
    def cap(self, ago_s):
        c = cfg(2, by_ram=True, max_jvms=8)
        b, d = ghidra_broker.settings(c)
        d.mkdir(parents=True, exist_ok=True)
        outlier(d, ago_s)
        err = io.StringIO()
        ghidra_peak._said[0] = ""
        with Machine(40.0) as m, mock.patch.object(ghidra_broker, "total_mb", lambda: 16000.0), \
                contextlib.redirect_stderr(err):
            m.procs, m.mem = [(5, 1, "java.exe", 1.0)], {5: 300.0}
            found = ghidra_broker.jvms(m.procs, lambda pid: True)
            n = ghidra_broker.cap(b, d, [], found, m.procs)
            ghidra_broker.cap(b, d, [], found, m.procs)  # asked again: said once
            for _ in range(50):  # the log is written on a thread of its own
                if (d / "ram_log.jsonl").exists():
                    break
                time.sleep(0.1)
        log = [json.loads(x) for x in (d / "ram_log.jsonl").read_text(encoding="utf-8").splitlines()]
        return n, err.getvalue(), log

    def test_fresh_outlier_is_said(self):
        n, err, log = self.cap(60.0)
        self.assertEqual(n, 1)
        self.assertEqual(err.count("1 of 8"), 1)
        self.assertIn("1 of 8", log[0]["note"])

    def test_stale_outlier_is_not_throttling(self):
        n, err, log = self.cap(10 * HOUR)
        self.assertEqual(n, 8)
        self.assertEqual(err, "")
        self.assertNotIn("note", log[0])


if __name__ == "__main__":
    unittest.main()
