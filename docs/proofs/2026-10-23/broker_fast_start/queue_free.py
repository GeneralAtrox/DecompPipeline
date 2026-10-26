"""Exit 0 once the shared Ghidra broker has no ticket waiting and RAM is under 85% (5 readings in a row, 1 s apart);
exit 1 after SECONDS. Reads the folder's file names only."""
import os, pathlib, sys, time, psutil
d = pathlib.Path(os.environ["LOCALAPPDATA"]) / "DecompPipeline" / "ghidra_broker"
end, calm = time.time() + float(sys.argv[1]), 0
while time.time() < end:
    ok = not list(d.glob("t_*")) and psutil.virtual_memory().percent < 85
    calm = calm + 1 if ok else 0
    if calm >= 5:
        sys.exit(0)
    time.sleep(1)
print("queue or RAM still busy"); sys.exit(1)
