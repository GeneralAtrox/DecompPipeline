"""Every 2 s until LOG.stop exists: RAM, the run's own Ghidra JVMs (java under a process whose command line names
WS), the broker's holds and tickets (the run's, and everyone's), as JSON lines. usage: slotwatch.py LOG WS"""
import json, os, pathlib, sys, time
import psutil
log, ws = pathlib.Path(sys.argv[1]), sys.argv[2]
stop = log.with_name(log.name + ".stop")
d = pathlib.Path(os.environ["LOCALAPPDATA"]) / "DecompPipeline" / "ghidra_broker"
with open(log, "a", encoding="utf-8") as f:
    while not stop.exists():
        mine, jvm_all, jvm_mine = set(), 0, 0
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.info["name"] == "python.exe" and ws in " ".join(p.info["cmdline"] or []):
                    mine.add(p.info["pid"])
            except Exception:
                pass
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.info["name"] == "java.exe" and "ghidra" in " ".join(p.info["cmdline"] or []).lower():
                    jvm_all += 1
                    if any(a.pid in mine for a in p.parents()):
                        jvm_mine += 1
            except Exception:
                pass
        files = [q.name for q in d.glob("[th]_*.json")]
        own = lambda k: sum(1 for n in files if n.startswith(k) and int(n.split("_")[3]) in mine)
        f.write(json.dumps({"t": round(time.time()), "ram": psutil.virtual_memory().percent, "jvm_mine": jvm_mine,
                            "jvm_all": jvm_all, "holds_mine": own("h_"), "tickets_mine": own("t_"),
                            "holds": sum(n.startswith("h_") for n in files),
                            "tickets": sum(n.startswith("t_") for n in files)}) + "\n")
        f.flush()
        time.sleep(2)
