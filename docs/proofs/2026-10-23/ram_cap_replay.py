"""Proof K (row 17): the sampled hour of the live queue replayed under the RAM-measured slot cap (core/ghidra_broker
by_ram), against the old rule (allowed: 2 JVMs, a 3rd under 82%, none from 88%).

The samples (one every 2 s: machine RAM %, each Ghidra JVM's working set and private MB, the broker's holds and
waiting tickets) are what really ran. At each sample the replay asks the new rule how many JVMs it would allow, with
every JVM it added earlier still running at the full peak (the worst case: a real JVM starts small and grows). A JVM
it adds serves a ticket that was really waiting and runs for the median life of the hour's prepare JVMs. Its RAM is
added to every later sample until it ends. So the replay's RAM is the real RAM plus a peak for each added JVM.
The samples count the waiting tickets but do not name them, so a ticket that waits through several samples could be
served twice. The gain is therefore measured as waiting time: at each sample, the added JVMs running serve that many
of the tickets really waiting then (never more); that ticket-time, divided by a prepare JVM's median life, is the
number of prepares started sooner.

usage: python ram_cap_replay.py SAMPLES.jsonl TOTAL_MB CORES OUT.json
DP_TREE names the DP tree whose core/ghidra_broker is replayed (default: this file's tree).
"""
import json
import os
import pathlib
import statistics
import sys

TREE = pathlib.Path(os.environ.get("DP_TREE") or pathlib.Path(__file__).resolve().parents[3])
sys.path.insert(0, str(TREE))
from core import ghidra_broker  # noqa: E402


def lives(samples):
    """Seconds each prepare JVM of the hour was seen running (first sample to last), for those that started and
    ended inside it."""
    first, last = {}, {}
    for s in samples:
        for pid, (_rss, _priv, kind, start) in s["j"].items():
            if kind == "prepare":
                first.setdefault((pid, start), s["t"])
                last[(pid, start)] = s["t"]
    t0, t1 = samples[0]["t"], samples[-1]["t"]
    return [last[k] - k[1] for k in first if k[1] > t0 and last[k] < t1]


def replay(samples, total, cores):
    new = {**ghidra_broker.DEFAULTS, "slots": 2, "by_ram": True}
    old = {**ghidra_broker.DEFAULTS, "slots": 2}
    life = statistics.median(lives(samples))
    peak, added, ends, served_s, waiting_s, jvms_real, jvms_new = 0.0, 0, [], 0.0, 0.0, 0.0, 0.0
    one = None
    ram_max, over85, over95, at_cap_old = 0.0, 0, 0, 0
    for k, s in enumerate(samples):
        t = s["t"]
        dt = min(10.0, samples[k + 1]["t"] - t) if k + 1 < len(samples) else 0.0
        ends = [e for e in ends if e > t]
        mb = [max(rss, priv) for rss, priv, _k, _s in s["j"].values()]
        peak = max([peak, *mb])
        p = peak or float(new["peak_mb"])
        one = 100.0 * p / total
        pending = max(0, s.get("h", 0) - len(mb))
        real = len(mb) + pending
        w = s.get("w", 0)
        cap_old = ghidra_broker.allowed(old, s["ram"])
        at_cap_old += bool(w) and real >= cap_old
        ram = s["ram"] + len(ends) * one
        cap = ghidra_broker.by_ram(new, ram, total, mb + [p] * len(ends), pending, p, cores=cores)
        start = max(0, min(w - len(ends), cap - real - len(ends)))  # tickets really waiting, not yet served
        ends += [t + life] * start
        added += start
        ram = s["ram"] + len(ends) * one
        waiting_s += w * dt
        served_s += sum(sorted((min(dt, e - t) for e in ends), reverse=True)[:w])
        jvms_real += real * dt
        jvms_new += (real + len(ends)) * dt
        ram_max = max(ram_max, ram)
        over85 += ram >= 85
        over95 += ram >= 95
    n = len(samples)
    return {"samples": n, "minutes": round((samples[-1]["t"] - samples[0]["t"]) / 60, 1), "total_mb": round(total),
            "cores": cores, "peak_jvm_mb": round(peak), "one_jvm_pct": round(one, 2),
            "median_prepare_jvm_life_s": round(life, 1), "real_ram_max": max(s["ram"] for s in samples),
            "real_ram_median": statistics.median(s["ram"] for s in samples),
            "jvms_running_median": statistics.median(len(s["j"]) for s in samples),
            "added_jvm_starts_upper_bound": added, "waiting_ticket_minutes": round(waiting_s / 60, 1),
            "waiting_ticket_minutes_served_sooner": round(served_s / 60, 1),
            "prepares_started_sooner": round(served_s / life),
            "share_at_cap_with_waiters_old": round(at_cap_old / n, 3),
            "jvms_running_mean_real": round(jvms_real / (samples[-1]["t"] - samples[0]["t"]), 2),
            "jvms_running_mean_replay": round(jvms_new / (samples[-1]["t"] - samples[0]["t"]), 2),
            "replay_ram_max": round(ram_max, 2), "samples_at_or_over_85": over85, "samples_at_or_over_95": over95}


def main(argv):
    samples = [json.loads(line) for line in open(argv[1], encoding="utf-8")]
    out = replay(samples, float(argv[2]), int(argv[3]))
    pathlib.Path(argv[4]).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv)
