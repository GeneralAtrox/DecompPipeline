"""Put the translated C of ADDRS into a dp_judge mirror as matched C (mirror/matched/<a>.c, ledger status matched).
usage: mutprep.py OUT WSNAME ADDRFILE [FALLBACK_C_DIR ...]  (OUT: dp_judge --out; a function OUT has no C for is
taken from the first fallback folder that has it: same-C functions only, which vcmp checked by c_sha256)"""
import json, pathlib, shutil, sys
out, ws, lst = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
srcs = [out / "translated" / ws] + [pathlib.Path(p) for p in sys.argv[4:]]
m = out / "mirror" / ws
(m / "matched").mkdir(exist_ok=True)
(m / "state").mkdir(exist_ok=True)
lp = m / "state" / "ledger.json"
led = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else {}
led.setdefault("functions", {})
for a in open(lst).read().split():
    src = next(d / f"{a}.c" for d in srcs if (d / f"{a}.c").exists())
    if src.parent != srcs[0]:
        print(f"{a}: C from {src.parent}")
    shutil.copy(src, m / "matched" / f"{a}.c")
    led["functions"].setdefault(a, {})["status"] = "matched"
lp.write_text(json.dumps(led), encoding="utf-8")
print(m)
